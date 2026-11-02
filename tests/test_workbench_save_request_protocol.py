"""Persistent candidate-save identities survive lost replies without new writes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

import test_workbench as base


class SaveRequestProtocolTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.w = base.story.workbench
        self.source = self.w._editor_document(self.root, 'formal:1')

    def payload(self, source=None, text=None, mode='working'):
        source = source or self.source
        return {
            'id': source['id'], 'sha256': source['sha256'], 'snapshot': source['snapshot'],
            'metadata_sha256': source.get('metadata_sha256'),
            'text': text if text is not None else source['text'] + '本次协议测试新增文字。\n',
            'save_mode': mode, 'request_id': uuid.uuid4().hex,
        }

    def status(self, payload):
        return self.w._editor_save_status(self.root, {'request_id': payload['request_id']})

    def files(self):
        result = {}
        for relative in ('.story/drafts/workbench', '.story/workbench-save-requests'):
            directory = self.root / relative
            if not directory.exists():
                continue
            for path in directory.rglob('*'):
                if path.is_file():
                    result[path.relative_to(self.root).as_posix()] = (
                        hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        return result

    def assert_replay_does_not_write(self, payload):
        before = self.files()
        try:
            result = self.w._editor_save(self.root, payload)
        except base.story.StoryError:
            result = None
        self.assertEqual(self.files(), before)
        return result

    def test_formal_save_repeated_identity_returns_same_candidate_without_writes(self):
        payload = self.payload()
        first = self.w._editor_save(self.root, payload)
        second = self.assert_replay_does_not_write(payload)
        self.assertIsNotNone(second)
        self.assertEqual(second['id'], first['id'])
        self.assertEqual(self.status(payload)['state'], 'saved')
        self.assertEqual(len(list((self.root / '.story/drafts/workbench').glob('*_候选_*.md'))), 1)

    def test_in_place_update_replay_does_not_revalidate_the_old_source_digest(self):
        created = self.w._editor_save(self.root, self.payload())
        candidate = self.w._editor_document(self.root, created['id'])
        payload = self.payload(candidate, candidate['text'] + '继续同一工作稿。\n')
        first = self.w._editor_save(self.root, payload)
        self.assertEqual(first['id'], candidate['id'])
        self.assertTrue(first['updated_existing'])
        self.assertNotEqual(self.w._editor_document(self.root, first['id'])['sha256'], payload['sha256'])
        second = self.assert_replay_does_not_write(payload)
        self.assertIsNotNone(second)
        self.assertEqual(second['id'], first['id'])
        self.assertEqual(self.status(payload)['state'], 'saved')

    def test_explicit_same_text_checkpoint_is_created_once(self):
        payload = self.payload(text=self.source['text'], mode='version')
        first = self.w._editor_save(self.root, payload)
        self.assertTrue(first['checkpoint'])
        replay = self.assert_replay_does_not_write(payload)
        self.assertIsNotNone(replay)
        self.assertEqual(replay['id'], first['id'])
        self.assertEqual(len(list((self.root / '.story/drafts/workbench').glob('*_候选_*.md'))), 1)

    def test_distinct_identities_keep_two_deliberately_retained_checkpoints(self):
        first = self.w._editor_save(self.root, self.payload(text=self.source['text'], mode='version'))
        second = self.w._editor_save(self.root, self.payload(text=self.source['text'], mode='version'))
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(len(list((self.root / '.story/drafts/workbench').glob('*_候选_*.md'))), 2)

    def test_invalid_identity_is_rejected_before_writing(self):
        payload = self.payload()
        before = self.files()
        for value in ('', 'short', '../' + 'a' * 32, 'a' * 81, None, 123):
            with self.subTest(value=value):
                invalid = {**payload, 'request_id': value}
                with self.assertRaises(base.story.StoryError):
                    self.w._editor_save(self.root, invalid)
                with self.assertRaises(base.story.StoryError):
                    self.status(invalid)
                self.assertEqual(self.files(), before)

    def test_saved_identity_is_bound_to_its_original_book(self):
        payload = self.payload()
        self.w._editor_save(self.root, payload)
        other = self.root.parent / 'another-synthetic-book'
        base.story.Book.create(other, '另一合成作品', 'long')
        relative = Path('.story/workbench-save-requests') / (payload['request_id'] + '.json')
        target = other / relative
        target.parent.mkdir(parents=True)
        target.write_bytes((self.root / relative).read_bytes())
        before = target.read_bytes()
        with self.assertRaises(base.story.StoryError):
            self.w._editor_save_status(other, {'request_id': payload['request_id']})
        self.assertEqual(target.read_bytes(), before)

    def test_identity_reuse_with_changed_payload_is_rejected_without_writing(self):
        payload = self.payload()
        self.w._editor_save(self.root, payload)
        for change in ({'text': payload['text'] + '另一版。'}, {'save_mode': 'version'},
                       {'id': 'formal:2'}, {'sha256': '0' * 64},
                       {'metadata_sha256': '0' * 64}, {'snapshot': 'different'}):
            with self.subTest(change=list(change)):
                before = self.files()
                with self.assertRaises(base.story.StoryError):
                    self.w._editor_save(self.root, {**payload, **change})
                self.assertEqual(self.files(), before)

    def test_unknown_identity_readback_is_not_started_and_creates_nothing(self):
        payload = self.payload()
        before = self.files()
        for _ in range(2):
            result = self.status(payload)
            self.assertEqual(result['state'], 'not_started')
            self.assertEqual(result['request_id'], payload['request_id'])
        self.assertEqual(self.files(), before)
        self.assertFalse((self.root / '.story/workbench-save-requests').exists())

    def test_saved_status_is_read_only_and_binds_both_files_and_body_text(self):
        payload = self.payload()
        receipt = self.w._editor_save(self.root, payload)
        before = self.files()
        for _ in range(2):
            status = self.status(payload)
            self.assertEqual(status['state'], 'saved')
            self.assertEqual(status['id'], receipt['id'])
            self.assertEqual(status['path'], receipt['path'])
            self.assertEqual(status['sha256'], hashlib.sha256((self.root / receipt['path']).read_bytes()).hexdigest())
            self.assertEqual(status['metadata_sha256'], hashlib.sha256((self.root / (receipt['path'] + '.meta.json')).read_bytes()).hexdigest())
            self.assertEqual(status['text_sha256'], hashlib.sha256(payload['text'].encode()).hexdigest())
        self.assertEqual(self.files(), before)

    def test_readback_survives_a_fresh_python_process(self):
        payload = self.payload()
        saved = self.w._editor_save(self.root, payload)
        before = self.files()
        program = '''
import importlib.util,json,sys
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('receipt_restart_story',sys.argv[1])
story=importlib.util.module_from_spec(spec);spec.loader.exec_module(story)
print(json.dumps(story.workbench._editor_save_status(sys.argv[2],{'request_id':sys.argv[3]})))
'''
        run = subprocess.run([sys.executable, '-B', '-c', program, str(base.TOOL),
                              str(self.root), payload['request_id']], capture_output=True,
                             text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result['state'], 'saved')
        self.assertEqual(result['id'], saved['id'])
        self.assertEqual(self.files(), before)

    def test_pending_cleanup_failure_does_not_authorize_a_second_save(self):
        payload = self.payload()
        with patch.object(self.w, '_finish_pending_save', side_effect=OSError('injected finish failure')):
            with self.assertRaises(base.story.StoryError):
                self.w._editor_save(self.root, payload)
        before = self.files()
        self.assertEqual(self.status(payload)['state'], 'pending')
        self.assertEqual(self.files(), before)
        self.assertIsNone(self.assert_replay_does_not_write(payload))
        self.assertEqual(len(list((self.root / '.story/drafts/workbench').glob('*_候选_*.md'))), 1)

    def test_body_failure_retains_identity_and_does_not_restart_write(self):
        payload = self.payload()
        atomic = self.w.api.atomic_write
        def fail_body(path, *args, **kwargs):
            if Path(path).name.endswith('.md') and '_候选_' in Path(path).name:
                raise OSError('injected body publication failure')
            return atomic(path, *args, **kwargs)
        with patch.object(self.w.api, 'atomic_write', side_effect=fail_body):
            with self.assertRaises(base.story.StoryError):
                self.w._editor_save(self.root, payload)
        self.assertEqual(self.status(payload)['state'], 'pending')
        self.assertIsNone(self.assert_replay_does_not_write(payload))
        self.assertEqual(len(list((self.root / '.story/drafts/workbench').glob('*_候选_*.md'))), 0)

    def test_metadata_failure_and_rollback_remain_unconfirmed_without_replay(self):
        payload = self.payload()
        atomic = self.w.api.atomic_write
        def fail_metadata(path, *args, **kwargs):
            if Path(path).name.endswith('.meta.json'):
                raise OSError('injected metadata failure')
            return atomic(path, *args, **kwargs)
        with patch.object(self.w.api, 'atomic_write', side_effect=fail_metadata):
            with self.assertRaises(base.story.StoryError):
                self.w._editor_save(self.root, payload)
        self.assertIn(self.status(payload)['state'], ('pending', 'changed'))
        self.assertIsNone(self.assert_replay_does_not_write(payload))
        self.assertEqual(len(list((self.root / '.story/drafts/workbench').glob('*_候选_*.md'))), 0)

    def test_external_body_change_is_not_acknowledged_or_overwritten(self):
        payload = self.payload()
        saved = self.w._editor_save(self.root, payload)
        target = self.root / saved['path']
        target.write_text(target.read_text() + '仅合成夹具的外部修改。\n')
        before = self.files()
        self.assertEqual(self.status(payload)['state'], 'changed')
        self.assertEqual(self.files(), before)
        self.assertIsNone(self.assert_replay_does_not_write(payload))

    def test_external_metadata_change_is_not_acknowledged_or_overwritten(self):
        payload = self.payload()
        saved = self.w._editor_save(self.root, payload)
        metadata = self.root / (saved['path'] + '.meta.json')
        content = json.loads(metadata.read_text())
        content['adopted'] = True
        metadata.write_text(json.dumps(content, ensure_ascii=False))
        before = self.files()
        self.assertEqual(self.status(payload)['state'], 'changed')
        self.assertEqual(self.files(), before)
        self.assertIsNone(self.assert_replay_does_not_write(payload))

    def test_older_operation_cannot_revert_a_later_successful_working_save(self):
        first_payload = self.payload()
        first = self.w._editor_save(self.root, first_payload)
        candidate = self.w._editor_document(self.root, first['id'])
        second_payload = self.payload(candidate, candidate['text'] + '第二次独立保存。\n')
        second = self.w._editor_save(self.root, second_payload)
        self.assertEqual(second['id'], first['id'])
        self.assertEqual(self.status(first_payload)['state'], 'changed')
        self.assertEqual(self.status(second_payload)['state'], 'saved')
        self.assertIsNone(self.assert_replay_does_not_write(first_payload))
        self.assertEqual(self.w._editor_document(self.root, second['id'])['text'], second_payload['text'])


if __name__ == '__main__':
    unittest.main()
