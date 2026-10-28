"""Chapter-linked assistant fragments retain their scope through editing."""
import hashlib
import json
import unittest

import test_workbench as base
import test_workbench_archives as archive_tests


class WorkbenchWritingScopeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.workbench = base.story.workbench
        self.path = '.story/drafts/workbench/助手候选-test.md'
        target = self.root / self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('她说了句话。\n', encoding='utf-8')
        meta = {'chapter': 2, 'content_kind': 'prose', 'writing_scope': 'fragment',
                'content_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
        (self.root / (self.path + '.meta.json')).write_text(json.dumps(meta), encoding='utf-8')

    def test_fragment_edit_save_recovery_and_review_keep_scope(self):
        doc = self.workbench._editor_document(self.root, 'file:' + self.path)
        self.assertEqual((doc['chapter'], doc['content_kind'], doc['writing_scope']), (2, 'prose', 'fragment'))
        metric = self.workbench._editor_metrics(self.root, {'id': doc['id'], 'text': doc['text']})
        self.assertIsNone(metric['target'])
        review = self.workbench._editor_review_task(self.root, doc['id'])
        self.assertEqual(review['check_scope'], 'fragment')
        self.assertIsNone(review['lint'])
        saved = self.workbench._editor_save(self.root, {'id': doc['id'], 'text': doc['text'] + '她转身。',
            'sha256': doc['sha256'], 'snapshot': doc['snapshot'], 'metadata_sha256': doc['metadata_sha256']})
        reopened = self.workbench._editor_document(self.root, saved['id'])
        self.assertEqual((reopened['chapter'], reopened['writing_scope']), (2, 'fragment'))
        recovered = self.workbench._editor_save(self.root, {'id': reopened['id'], 'text': reopened['text'],
            'title': reopened['title'], 'recovery_key': '1234567890abcdef', 'sha256': reopened['sha256'],
            'chapter': reopened['chapter'], 'content_kind': reopened['content_kind'],
            'writing_scope': reopened['writing_scope'], 'base_sha256': reopened.get('base_sha256'),
            'prefix': reopened.get('prefix', '')}, recovery=True)
        recovery = self.workbench._editor_document(self.root, recovered['id'])
        self.assertEqual((recovery['chapter'], recovery['writing_scope']), (2, 'fragment'))
        self.assertIsNone(self.workbench._editor_metrics(self.root, {'id': recovery['id'], 'text': recovery['text']})['target'])

    def test_fragment_counts_all_body_lines_and_excludes_chapter_heading(self):
        plan = self.fixture.book.get_plan(2)
        plan.update(count_method='visible_nonspace_v2', count_title=False, length=[2000, None])
        self.fixture.book.save_plan(2, plan, self.fixture.book.meta('revision'))
        target = self.root / self.path
        for original in ('甲乙。', '甲乙。\n丙丁。', '第2章 雨夜\n甲乙。\n丙丁。', '甲\u200b乙\n丙。'):
            target.write_text(original, encoding='utf-8')
            doc = self.workbench._editor_document(self.root, 'file:' + self.path)
            metric = self.workbench._editor_metrics(self.root, {'id': doc['id'], 'text': doc['text'], 'selection': '甲乙。'})
            body = doc['text'].split('\n', 1)[1] if doc['text'].startswith('第2章 ') else doc['text']
            expected = base.story.manuscript_counts('\n' + body, True)['visible_nonspace_v2']
            self.assertEqual(metric['count'], expected)
            self.assertEqual(metric['selection'], 3)
            self.assertEqual(metric['writing_scope'], 'fragment')
            self.assertFalse(metric['include_title'])
            self.assertIsNone(metric['target'])
            self.assertEqual(expected, 4 if '\u200b' in original else 6 if '丙丁' in original else 3)

    def test_fragment_heading_after_invisible_prefix_is_not_counted(self):
        plan = self.fixture.book.get_plan(2)
        plan.update(count_method='visible_nonspace_v2', count_title=False, length=[2000, None])
        self.fixture.book.save_plan(2, plan, self.fixture.book.meta('revision'))
        target = self.root / self.path
        for prefix in ('\n', '\u200b\n', '\u0301'):
            original = prefix + '第2章 雨夜\n甲乙。'
            target.write_text(original, encoding='utf-8')
            doc = self.workbench._editor_document(self.root, 'file:' + self.path)
            metrics = self.workbench._editor_metrics(self.root, {'id': doc['id'], 'text': doc['text']})
            self.assertEqual(metrics['count'], 3)
            self.assertFalse(metrics['include_title'])
            self.assertEqual(target.read_text(encoding='utf-8'), original)

    def test_complete_chapter_target_and_navigation(self):
        plan = self.fixture.book.get_plan(2)
        plan.update(count_method='visible_nonspace_v2', count_title=False, length=[2000, None])
        self.fixture.book.save_plan(2, plan, self.fixture.book.meta('revision'))
        metadata = self.root / (self.path + '.meta.json')
        value = json.loads(metadata.read_text(encoding='utf-8'))
        value['writing_scope'] = 'complete_chapter'
        metadata.write_text(json.dumps(value), encoding='utf-8')
        doc = self.workbench._editor_document(self.root, 'file:' + self.path)
        for count, allowed in ((1999, False), (2000, True), (2801, True)):
            metric = self.workbench._editor_metrics(self.root, {'id': doc['id'], 'text': '甲' * count})
            self.assertEqual(metric['target'], [2000, None])
            self.assertEqual(metric['in_range'], allowed)
        catalog = self.workbench._editor_catalog(self.root)
        rows = [row for group in catalog['workspace']['groups'] for row in group['items']]
        row = next(row for row in rows if row['id'] == doc['id'])
        self.assertEqual((row['chapter'], row['writing_scope']), (2, 'complete_chapter'))

    def test_pending_recovery_retains_scope(self):
        meta = {'chapter': 2, 'content_kind': 'prose', 'writing_scope': 'fragment', 'prefix': ''}
        relative = '.story/drafts/workbench/待核对.md'
        pending = self.workbench._start_pending_save(self.root, relative, '片段。', '片段。', meta)
        try:
            doc = self.workbench._editor_document(self.root, 'pending:' + relative + '.pending.json')
            self.assertEqual((doc['chapter'], doc['writing_scope'], doc['needs_recovery']), (2, 'fragment', True))
            saved = self.workbench._editor_save(self.root, {'id': doc['id'], 'text': doc['text'],
                'sha256': doc['sha256'], 'snapshot': doc['snapshot'], 'metadata_sha256': doc['metadata_sha256']})
            reopened = self.workbench._editor_document(self.root, saved['id'])
            self.assertEqual((reopened['chapter'], reopened['writing_scope']), (2, 'fragment'))
        finally:
            self.workbench._finish_pending_save(self.root, pending)

    def test_archive_roundtrip_preserves_scope_sidecar(self):
        fixture = archive_tests.WorkbenchArchivesTests('runTest')
        fixture.setUp()
        try:
            body = '片段。'.encode()
            path = '.story/drafts/workbench/助手候选-test.md'
            meta = {'chapter': 2, 'content_kind': 'prose', 'writing_scope': 'fragment',
                    'content_sha256': hashlib.sha256(body).hexdigest()}
            fixture.write(path, body)
            fixture.write(path + '.meta.json', json.dumps(meta).encode())
            fixture.archive()
            fixture.archives.restore_archive(fixture.output, fixture.destination)
            doc = self.workbench._editor_document(fixture.destination, 'file:' + path)
            self.assertEqual((doc['chapter'], doc['writing_scope']), (2, 'fragment'))
        finally:
            fixture.doCleanups()

    def test_invalid_scope_is_not_silently_reinterpreted(self):
        path = self.root / (self.path + '.meta.json')
        path.write_text(json.dumps({'chapter': 2, 'writing_scope': 'invented'}), encoding='utf-8')
        doc = self.workbench._editor_document(self.root, 'file:' + self.path)
        self.assertFalse(doc['editable'])
        self.assertEqual(doc['metadata_error']['code'], 'workbench_metadata_invalid')


class NativeCandidateIntegrationTests(unittest.TestCase):
    def test_native_receipt_to_editor_for_each_scope(self):
        import test_host_execution as host_tests
        story = base.story
        for scope in ('complete_chapter', 'fragment', 'no_chapter', 'legacy'):
            with self.subTest(scope=scope):
                host = host_tests.ManagedHostTests('runTest')
                host.setUp()
                try:
                    if scope == 'complete_chapter':
                        host._writing_scope_task(complete=True)
                        chapter, candidate = 3, '第3章 门内\n' + '甲' * 2801
                    else:
                        chapter = None if scope == 'no_chapter' else 2
                        host.task = host.tasks.create(host.root, 'writing', '按请求写作。', [], chapter=chapter)['task']
                        candidate = '她停在门边。' if scope != 'legacy' else '第2章 雨夜\n她停在门边。'
                        if scope == 'legacy':
                            path = host.root / host.tasks.DIRECTORY / (host.task['id'] + '.json')
                            record = json.loads(path.read_text(encoding='utf-8'))
                            record['binding'].pop('complete_chapter')
                            previous = record['binding_sha256']
                            record['binding_sha256'] = story.digest(story.dumps(record['binding']))
                            record['prompt'] = record['prompt'].replace(previous, record['binding_sha256'])
                            path.write_text(story.dumps(record), encoding='utf-8')
                            host.task = host.tasks.get(host.root, host.task['id'])['task']
                    packet = host.start('integration-' + scope)
                    done = host.finish(packet, value={'summary': '候选待作者核对。', 'issues': [], 'candidate_text': candidate})
                    self.assertEqual(done['execution']['state'], 'completed')
                    path = done['task']['output']['path']
                    meta = json.loads((host.root / (path + '.meta.json')).read_text(encoding='utf-8'))
                    self.assertEqual(meta['chapter'], chapter)
                    self.assertEqual(meta.get('writing_scope'), None if scope == 'no_chapter' else scope)
                    doc = story.workbench._editor_document(host.root, 'file:' + path)
                    self.assertEqual(doc.get('chapter'), chapter)
                    self.assertEqual(doc.get('writing_scope'), None if scope == 'no_chapter' else scope)
                    metrics = story.workbench._editor_metrics(host.root, {'id': doc['id'], 'text': doc['text']})
                    if scope in ('fragment', 'no_chapter'):
                        self.assertIsNone(metrics['target'])
                    elif scope == 'complete_chapter':
                        self.assertEqual(metrics['target'], [2000, None])
                        self.assertTrue(metrics['in_range'])
                    review = story.workbench._editor_review_task(host.root, doc['id'])
                    self.assertEqual(review['check_scope'], {'fragment': 'fragment', 'no_chapter': 'material',
                        'legacy': 'complete_chapter', 'complete_chapter': 'complete_chapter'}[scope])
                    saved = story.workbench._editor_save(host.root, {'id': doc['id'], 'text': doc['text'] + '她继续往前。',
                        'sha256': doc['sha256'], 'snapshot': doc['snapshot'], 'metadata_sha256': doc['metadata_sha256']})
                    reopened = story.workbench._editor_document(host.root, saved['id'])
                    self.assertEqual(reopened.get('chapter'), chapter)
                    self.assertEqual(reopened.get('writing_scope'), None if scope == 'no_chapter' else scope)
                finally:
                    host.doCleanups()
