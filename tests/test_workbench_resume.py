"""Position-only resume records remain bound to the current book and file."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_workbench as base


class WorkbenchResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workbench-resume-')
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name).resolve()
        self.root = self.area / '本书'
        base.story.Book.create(self.root, '位置验收', 'long')
        self.w = base.story.workbench
        self.source = self.root / '创作约定.md'
        self.source.write_text('中文😀第一行\n第二行。', encoding='utf-8')
        book = self.w._ReadOnlyBook(self.root)
        try:
            self.book_id = book.meta('id')
        finally:
            book.close()
        self.path = self.root / '.story/workbench-resume.json'

    def payload(self, fingerprint=None, **changes):
        value = {'book_id': self.book_id, 'expected_state_sha256': fingerprint,
                 'document_id': 'file:创作约定.md', 'document_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest(),
                 'mode': 'editing', 'cursor': {'start': 2, 'end': 4, 'direction': 'backward'},
                 'scroll': {'main': 81.25, 'editor': 120}, 'buffer_dirty': False}
        value.update(changes)
        return value

    def save(self, value=None):
        return self.w._editor_resume_save(self.root, value or self.payload(), self.book_id)

    def read(self):
        return self.w._editor_resume_state(self.root, self.book_id)

    def meta(self):
        book = self.w._ReadOnlyBook(self.root)
        try:
            return list(map(tuple, book.db.execute('SELECT key,value FROM meta ORDER BY key')))
        finally:
            book.close()

    def test_position_survives_independent_reads_without_body_token_or_meta_changes(self):
        before, body = self.meta(), self.source.read_bytes()
        self.assertEqual(self.read()['status'], 'empty')
        saved = self.save()
        loaded = self.read()
        self.assertEqual(loaded['status'], 'ready')
        self.assertEqual(saved['state'], loaded['state'])
        self.assertEqual(loaded['document'], {'title': '创作约定', 'kind': 'material', 'chapter': None})
        self.assertEqual(loaded['state_sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        record = json.loads(self.path.read_bytes())
        self.assertEqual(set(record), {'version', 'book_id', 'document_id', 'document_sha256', 'mode',
                                       'cursor', 'scroll', 'buffer_dirty', 'updated_at'})
        self.assertNotIn('中文', self.path.read_text())
        self.assertNotIn('token', self.path.read_text())
        self.assertEqual((self.meta(), self.source.read_bytes()), (before, body))
        self.assertEqual(list(self.path.parent.glob('.workbench-resume-*')), [])

    def test_old_session_cannot_overwrite_newer_position_and_retry_uses_new_cas(self):
        first = self.save()
        current = self.path.read_bytes()
        with self.assertRaises(base.story.StoryError) as raised:
            self.save(self.payload(scroll={'main': 999, 'editor': 0}))
        self.assertEqual(raised.exception.code, 'workbench_resume_conflict')
        self.assertEqual(self.path.read_bytes(), current)
        second = self.save(self.payload(first['state_sha256'], scroll={'main': 999, 'editor': 0}))
        self.assertEqual(self.read()['state']['scroll']['main'], 999)
        with self.assertRaises(base.story.StoryError):
            self.save(self.payload(first['state_sha256']))
        self.assertEqual(self.read()['state_sha256'], second['state_sha256'])

    def test_changed_or_deleted_target_is_explained_without_touching_position_or_source(self):
        saved = self.save()
        persisted = self.path.read_bytes()
        self.source.write_text('外部已经修改的新版本。', encoding='utf-8')
        changed = self.read()
        self.assertEqual(changed['status'], 'changed')
        self.assertEqual(changed['document']['title'], '创作约定')
        with self.assertRaises(base.story.StoryError) as raised:
            self.save({**self.payload(saved['state_sha256']), 'document_sha256': saved['state']['document_sha256']})
        self.assertEqual(raised.exception.code, 'workbench_resume_changed')
        self.source.unlink()
        self.assertEqual(self.read()['status'], 'missing')
        self.assertEqual(self.path.read_bytes(), persisted)

    def test_other_book_record_or_server_identity_never_resumes_or_writes(self):
        self.save()
        record = json.loads(self.path.read_text())
        record['book_id'] = 'another-book'
        self.path.write_text(json.dumps(record), encoding='utf-8')
        self.assertEqual(self.read()['status'], 'invalid')
        original = self.path.read_bytes()
        with patch.object(self.w, '_editor_resume_document') as resolver:
            with self.assertRaises(base.story.StoryError) as raised:
                self.w._editor_resume_state(self.root, 'different-service-book')
            self.assertEqual(raised.exception.code, 'workbench_book_changed')
            resolver.assert_not_called()
        with self.assertRaises(base.story.StoryError):
            self.save(self.payload(book_id='another-book'))
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_unknown_fields_and_paths_never_get_persisted(self):
        cases = [dict(text='不要保存正文'), dict(token='secret'), dict(root='/outside'),
                 dict(document_id='file:../outside.md'), dict(document_id='file:/outside.md'),
                 dict(document_id='external:scope:outside.md'), dict(document_id='https://example.test/token'),
                 dict(document_id='file:不存在.md'), dict(document_sha256='bad'),
                 dict(scroll={'main': float('nan'), 'editor': 0}),
                 dict(cursor={'start': 7, 'end': 3, 'direction': 'none'})]
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(base.story.StoryError):
                    self.save(self.payload(**changes))
                self.assertFalse(self.path.exists())

    def test_corrupt_oversize_or_linked_record_is_explained_and_left_intact(self):
        for raw in (b'{invalid', b'x' * 16385, b'[]'):
            with self.subTest(raw_size=len(raw)):
                self.path.write_bytes(raw)
                self.assertEqual(self.read()['status'], 'invalid')
                self.assertEqual(self.path.read_bytes(), raw)
        self.path.unlink()
        outside = self.area / 'outside.json'
        outside.write_text('outside secret', encoding='utf-8')
        self.path.symlink_to(outside)
        self.assertEqual(self.read()['status'], 'invalid')
        with self.assertRaises(base.story.StoryError):
            self.save()
        self.assertEqual(outside.read_text(), 'outside secret')
        self.assertTrue(self.path.is_symlink())

    def test_saved_utf16_cursor_is_clamped_and_dirty_flag_does_not_claim_text_recovery(self):
        fingerprint = None
        for newline in ('\n', '\r\n'):
            with self.subTest(newline=repr(newline)):
                text = '中文😀第一行' + newline + '第二行。'
                raw = text.encode('utf-8')
                self.source.write_bytes(raw)
                saved = self.save(self.payload(fingerprint, buffer_dirty=True,
                                               cursor={'start': 100, 'end': 500, 'direction': 'none'}))
                # The service returns exact file text. read_text() would fold
                # CRLF into LF and silently change this UTF-16 length oracle.
                length = len(text.encode('utf-16-le')) // 2
                expected = {'start': length, 'end': length, 'direction': 'none'}
                self.assertEqual(saved['state']['cursor'], expected)
                loaded = self.read()
                self.assertEqual(loaded['state']['cursor'], expected)
                self.assertIn('这里只定位已保存文稿', loaded['note'])
                self.assertIn('恢复中心', loaded['note'])
                self.assertEqual(self.source.read_bytes(), raw)
                fingerprint = saved['state_sha256']

    @unittest.skipIf(os.name == 'nt', 'POSIX descriptor/path replacement race; Windows pins deny rename')
    def test_replaced_book_directory_cannot_receive_original_books_resume_record(self):
        replacement = self.area / '另一部作品'
        base.story.Book.create(replacement, '另一部作品', 'short')
        previous = self.area / '原书已移动'
        original_stage = self.w.api._bound_stage
        swapped = False
        def swap(directory, prefix):
            nonlocal swapped
            result = original_stage(directory, prefix)
            if prefix == '.workbench-resume-stage-' and not swapped:
                swapped = True
                self.root.rename(previous)
                replacement.rename(self.root)
            return result
        payload = self.payload()
        with patch.object(self.w.api, '_bound_stage', side_effect=swap):
            with self.assertRaises(base.story.StoryError):
                self.save(payload)
        self.assertTrue(swapped)
        self.assertFalse(self.path.exists())
        self.assertFalse((previous / '.story/workbench-resume.json').exists())
        self.assertEqual(list((previous / '.story').glob('.workbench-resume-*')), [])

    def test_displaced_concurrent_position_is_restored_without_overwriting_it(self):
        first = self.save()
        concurrent = copy.deepcopy(first['state'])
        concurrent['scroll']['main'] = 567
        concurrent_raw = json.dumps(concurrent, ensure_ascii=False).encode()
        original_replace = self.w.api._bound_replace
        fired = False
        def concurrent_replace(source, target):
            nonlocal fired
            if source.name == 'workbench-resume.json' and not fired:
                fired = True
                self.path.write_bytes(concurrent_raw)
            return original_replace(source, target)
        with patch.object(self.w.api, '_bound_replace', side_effect=concurrent_replace):
            with self.assertRaises(base.story.StoryError) as raised:
                self.save(self.payload(first['state_sha256']))
        self.assertEqual(raised.exception.code, 'workbench_resume_conflict')
        self.assertEqual(self.path.read_bytes(), concurrent_raw)

    def test_concurrent_oversize_record_is_not_fully_read_or_overwritten(self):
        first = self.save()
        concurrent_raw = b'x' * 40000
        original_replace = self.w.api._bound_replace
        fired = False
        def concurrent_replace(source, target):
            nonlocal fired
            if source.name == 'workbench-resume.json' and not fired:
                fired = True
                self.path.write_bytes(concurrent_raw)
            return original_replace(source, target)
        with patch.object(self.w.api, '_bound_replace', side_effect=concurrent_replace):
            with self.assertRaises(base.story.StoryError):
                self.save(self.payload(first['state_sha256']))
        self.assertEqual(self.path.read_bytes(), concurrent_raw)
        self.assertEqual(self.read()['status'], 'invalid')

    def test_read_only_image_target_never_restores_editing_mode(self):
        image = self.root / '封面.png'
        image.write_bytes(b'synthetic image bytes')
        saved = self.save(self.payload(document_id='file:封面.png',
                                       document_sha256=hashlib.sha256(image.read_bytes()).hexdigest()))
        self.assertEqual(saved['state']['mode'], 'reading')
        self.assertEqual(self.read()['state']['mode'], 'reading')


if __name__ == '__main__':
    unittest.main()
