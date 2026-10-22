"""Continuing a working draft retains checkpoints and handles stale writes."""
import hashlib
import json
import unittest
from unittest.mock import patch

import test_workbench as base


class WorkingCandidateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = base.story.workbench
        self.root = self.fixture.root
        self.source = self.w._editor_document(self.root, 'formal:1')
        self.formal = (self.root / self.source['path']).read_bytes()

    def save(self, source, text, mode='working'):
        return self.w._editor_save(self.root, {**source, 'text': text, 'save_mode': mode})

    def test_repeated_working_saves_keep_one_candidate_and_original_lineage(self):
        first = self.save(self.source, '第一版文字。')
        candidate = self.w._editor_document(self.root, first['id'])
        original_meta = json.loads((self.root / (first['path'] + '.meta.json')).read_text())
        for value in ('第二版文字。', '第三版文字。'):
            saved = self.save(candidate, value)
            self.assertEqual(saved['id'], first['id'])
            self.assertTrue(saved['updated_existing'])
            candidate = self.w._editor_document(self.root, saved['id'])
            self.assertEqual(candidate['text'], value)
        files = list((self.root / '.story/drafts/workbench').glob('*.md'))
        self.assertEqual(len(files), 1)
        meta = json.loads((self.root / (first['path'] + '.meta.json')).read_text())
        for field in ('source', 'source_sha256', 'created', 'base_sha256'):
            self.assertEqual(meta[field], original_meta[field])
        self.assertEqual((self.root / self.source['path']).read_bytes(), self.formal)
        self.assertTrue(list((self.root / '.story/drafts/workbench/.backups').rglob('previous')))

    def test_checkpoint_can_be_created_unchanged_and_is_never_updated_in_place(self):
        first = self.save(self.source, '工作稿。')
        candidate = self.w._editor_document(self.root, first['id'])
        checkpoint = self.save(candidate, candidate['text'], 'version')
        self.assertNotEqual(checkpoint['id'], first['id'])
        retained = self.w._editor_document(self.root, checkpoint['id'])
        self.assertTrue(retained['checkpoint'])
        before = (self.root / checkpoint['path']).read_bytes()
        continuation = self.save(retained, '保留版本之后的修改。')
        self.assertNotEqual(continuation['id'], checkpoint['id'])
        self.assertFalse(continuation['checkpoint'])
        self.assertEqual((self.root / checkpoint['path']).read_bytes(), before)

    def test_old_payload_preserves_legacy_new_file_behavior(self):
        first = self.save(self.source, '工作稿。')
        candidate = self.w._editor_document(self.root, first['id'])
        second = self.w._editor_save(self.root, {**candidate, 'text': '旧接口的修改。'})
        self.assertNotEqual(second['id'], first['id'])
        self.assertFalse(second['updated_existing'])

    def test_external_change_is_not_overwritten(self):
        first = self.save(self.source, '工作稿。')
        candidate = self.w._editor_document(self.root, first['id'])
        path = self.root / first['path']
        path.write_text(candidate.get('prefix', '') + '外部修改。')
        with self.assertRaises(base.story.StoryError) as caught:
            self.save(candidate, '旧窗口内容。')
        self.assertEqual(caught.exception.code, 'stale_snapshot')
        self.assertIn('外部修改。', path.read_text())

    def test_metadata_failure_restores_previous_working_candidate(self):
        first = self.save(self.source, '原工作稿。')
        candidate = self.w._editor_document(self.root, first['id'])
        body = self.root / first['path']
        metadata = self.root / (first['path'] + '.meta.json')
        before = body.read_bytes(), metadata.read_bytes()
        atomic_write = self.w.api.atomic_write
        def fail_metadata(path, *args, **kwargs):
            if path == metadata:
                raise OSError('synthetic metadata failure')
            return atomic_write(path, *args, **kwargs)
        with patch.object(self.w.api, 'atomic_write', side_effect=fail_metadata):
            with self.assertRaises(base.story.StoryError) as caught:
                self.save(candidate, '未完成的新修改。')
        self.assertEqual(caught.exception.code, 'workbench_save_rolled_back')
        self.assertEqual((body.read_bytes(), metadata.read_bytes()), before)
        self.assertEqual(self.w._editor_document(self.root, first['id'])['text'], candidate['text'])

    def test_recovery_explicit_version_keeps_recovery_source(self):
        recovery = self.w._editor_save(self.root, {**self.source, 'text': '找回的文字。',
            'recovery_key': 'isolated-working-recovery-1234'}, recovery=True)
        opened = self.w._editor_document(self.root, recovery['id'])
        saved = self.save(opened, opened['text'], 'version')
        self.assertNotEqual(saved['id'], recovery['id'])
        self.assertTrue((self.root / recovery['path']).exists())
        self.assertEqual(self.w._editor_document(self.root, saved['id'])['text'], opened['text'])


if __name__ == '__main__':
    unittest.main()
