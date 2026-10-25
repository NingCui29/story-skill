"""Working saves retain both user text and foreign writes through failures."""
import hashlib
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import test_workbench as base


class WorkingCandidateRaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w, self.root = base.story.workbench, self.fixture.root
        source = self.w._editor_document(self.root, 'formal:1')
        saved = self.w._editor_save(self.root, {**source, 'text': '原有工作稿。', 'save_mode': 'working'})
        self.candidate = self.w._editor_document(self.root, saved['id'])
        self.body = self.root / saved['path']
        self.metadata = self.root / (saved['path'] + '.meta.json')
        self.pending = self.root / (saved['path'] + '.pending.json')
        self.before = self.body.read_bytes(), self.metadata.read_bytes()
        self.submitted = '本次保存的全部新文字。'

    def save(self):
        return self.w._editor_save(self.root, {**self.candidate, 'text': self.submitted, 'save_mode': 'working'})

    def assert_rescuable_pending(self):
        self.assertTrue(self.pending.exists())
        record = json.loads(self.pending.read_text())
        self.assertEqual(record['text'], self.submitted)
        restored = self.w._pending_document(self.root, 'pending:' + self.pending.relative_to(self.root).as_posix())
        self.assertEqual(restored['text'], self.submitted)
        self.assertTrue(restored['needs_recovery'])
        self.assertFalse(self.w._editor_document(self.root, self.candidate['id'])['editable'])

    def displayed_titles(self, documents):
        node = shutil.which('node')
        if not node:
            self.fail('Node.js is required for actual title presentation acceptance')
        page = self.w._editor_page(self.w.snapshot(self.fixture.book), {}, 'title-regression').decode()
        script = page.split('<script>', 1)[1].split('</script>', 1)[0]
        source = script[script.index('function displayTitle('):script.index('function versionLabel(')]
        program = source + '\nconst documents=' + json.dumps(documents, ensure_ascii=False)
        program += ';console.log(JSON.stringify(documents.map(displayTitle)));'
        result = subprocess.run([node, '-'], input=program, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_repeated_checkpoints_use_one_internal_suffix_and_a_stable_display_title(self):
        original_title = self.displayed_titles([self.candidate])[0]
        documents = []
        current = self.candidate
        for _ in range(4):
            saved = self.w._editor_save(self.root, {**current, 'text': current['text'], 'save_mode': 'version'})
            self.assertNotEqual(saved['id'], current['id'])
            self.assertEqual(Path(saved['path']).stem.count('_候选_'), 1)
            self.assertRegex(Path(saved['path']).stem, r'_候选_[0-9a-f]{12}$')
            current = self.w._editor_document(self.root, saved['id'])
            self.assertTrue(current['checkpoint'])
            documents.append(current)
        self.assertEqual(self.displayed_titles(documents), [original_title] * 4)

    def test_recovery_uuid_and_repeated_candidate_suffixes_are_removed_before_retaining_a_version(self):
        title = '雨夜定位_候选_0123456789ab_候选_cdef01234567'
        for key in ('11111111-2222-4333-8444-555555555555', 'abcdef0123456789abcdef0123456789'):
            with self.subTest(recovery_key=key):
                recovery = self.w._editor_save(self.root, {**self.candidate, 'text': '恢复出来的全部文字。',
                    'title': title, 'recovery_key': key}, recovery=True)
                opened = self.w._editor_document(self.root, recovery['id'])
                before = (self.root / recovery['path']).read_bytes()
                self.assertEqual(self.displayed_titles([opened]), ['雨夜定位'])
                saved = self.w._editor_save(self.root, {**opened, 'text': opened['text'], 'save_mode': 'version'})
                self.assertRegex(Path(saved['path']).name, r'^雨夜定位_候选_[0-9a-f]{12}\.md$')
                retained = self.w._editor_document(self.root, saved['id'])
                self.assertEqual(self.displayed_titles([retained]), ['雨夜定位'])
                self.assertEqual(retained['text'], opened['text'])
                self.assertEqual((self.root / recovery['path']).read_bytes(), before)

    def test_ordinary_material_title_that_resembles_an_internal_suffix_is_not_stripped(self):
        title = '策划材料_候选_0123456789ab'
        relative = title + '.md'
        path = self.root / relative
        path.write_text('作者主动命名的策划材料。')
        original = self.w._editor_document(self.root, 'file:' + relative)
        self.assertEqual(self.displayed_titles([original]), [title])
        saved = self.w._editor_save(self.root, {**original, 'text': original['text'], 'save_mode': 'version'})
        self.assertRegex(Path(saved['path']).name, '^' + title + r'_候选_[0-9a-f]{12}\.md$')
        self.assertEqual(path.read_text(), original['text'])
        self.assertEqual(self.displayed_titles([self.w._editor_document(self.root, original['id'])]), [title])

    def test_body_cas_failure_preserves_external_body_and_complete_pending_text(self):
        original_write = self.w.api.atomic_write
        external = self.candidate.get('prefix', '') + '外部编辑器在写入前保存的文字。'
        def external_before_body(path, *args, **kwargs):
            if Path(path) == self.body:
                self.body.write_text(external)
            return original_write(path, *args, **kwargs)
        with patch.object(self.w.api, 'atomic_write', side_effect=external_before_body):
            with self.assertRaises(base.story.StoryError) as caught:
                self.save()
        self.assertEqual(caught.exception.code, 'workbench_partial_save')
        self.assertEqual(self.body.read_text(), external)
        self.assertEqual(self.metadata.read_bytes(), self.before[1])
        self.assert_rescuable_pending()

    def test_metadata_cas_failure_preserves_external_record_and_restores_prior_body(self):
        original_write = self.w.api.atomic_write
        external = json.loads(self.before[1])
        external['external_note'] = '另一个编辑器更新的来源说明。'
        raw = json.dumps(external, ensure_ascii=False).encode()
        def external_before_metadata(path, *args, **kwargs):
            if Path(path) == self.metadata:
                self.metadata.write_bytes(raw)
            return original_write(path, *args, **kwargs)
        with patch.object(self.w.api, 'atomic_write', side_effect=external_before_metadata):
            with self.assertRaises(base.story.StoryError) as caught:
                self.save()
        self.assertEqual(caught.exception.code, 'workbench_partial_save')
        self.assertEqual(self.body.read_bytes(), self.before[0])
        self.assertEqual(self.metadata.read_bytes(), raw)
        self.assert_rescuable_pending()

    def test_late_metadata_write_failure_rolls_back_both_body_and_published_metadata(self):
        original_write = self.w.api.atomic_write
        failed = False
        def fail_after_metadata(path, *args, **kwargs):
            nonlocal failed
            result = original_write(path, *args, **kwargs)
            if Path(path) == self.metadata and not failed:
                failed = True
                raise OSError('metadata publication acknowledgment lost')
            return result
        with patch.object(self.w.api, 'atomic_write', side_effect=fail_after_metadata):
            with self.assertRaises(base.story.StoryError) as caught:
                self.save()
        self.assertEqual(caught.exception.code, 'workbench_save_rolled_back')
        self.assertEqual((self.body.read_bytes(), self.metadata.read_bytes()), self.before)
        self.assertFalse(self.pending.exists())
        self.assertEqual(self.w._editor_document(self.root, self.candidate['id'])['text'], self.candidate['text'])

    def test_external_body_after_publication_is_not_removed_during_metadata_rollback(self):
        original_write = self.w.api.atomic_write
        external = self.candidate.get('prefix', '') + '外部编辑器在回退前写入的文字。'
        def fail_metadata_with_external_body(path, *args, **kwargs):
            if Path(path) == self.metadata:
                self.body.write_text(external)
                raise OSError('metadata unavailable')
            return original_write(path, *args, **kwargs)
        with patch.object(self.w.api, 'atomic_write', side_effect=fail_metadata_with_external_body):
            with self.assertRaises(base.story.StoryError) as caught:
                self.save()
        self.assertEqual(caught.exception.code, 'workbench_partial_save')
        self.assertEqual(self.body.read_text(), external)
        self.assertEqual(self.metadata.read_bytes(), self.before[1])
        self.assert_rescuable_pending()

    def test_a_pending_other_save_cannot_be_replaced_or_resubmitted_from_the_old_snapshot(self):
        pending_record = {'version': 1, 'target': self.candidate['path'], 'text': '另一窗口待核对的文字。',
                          'meta': json.loads(self.before[1])}
        content = pending_record['meta'].get('prefix', '') + pending_record['text']
        pending_record['content_sha256'] = hashlib.sha256(content.encode()).hexdigest()
        foreign = json.dumps(pending_record, ensure_ascii=False).encode()
        self.pending.write_bytes(foreign)
        with self.assertRaises(base.story.StoryError) as caught:
            self.save()
        self.assertEqual(caught.exception.code, 'invalid_input')
        self.assertEqual(self.pending.read_bytes(), foreign)
        self.assertEqual((self.body.read_bytes(), self.metadata.read_bytes()), self.before)
        opened = self.w._pending_document(self.root, 'pending:' + self.pending.relative_to(self.root).as_posix())
        self.assertEqual(opened['text'], '另一窗口待核对的文字。')


if __name__ == '__main__':
    unittest.main()
