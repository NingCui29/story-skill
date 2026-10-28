"""Actual persisted handoffs, immutable evidence and stale-result boundaries."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import unittest
import http.client
import threading
from unittest.mock import patch

import test_workbench as base
from outline_fixture import bind_adopted_outline


story = base.story


class AssistantTaskTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.book = self.fixture.root, self.fixture.book
        self.tasks = story.tasks

    def material(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode('utf-8'))
        return {'path': path, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}

    def task(self, chapter=2):
        result = story.workbench._editor_review_task(self.root, f'formal:{chapter}')
        self.assertEqual(result['prompt'], result['task']['prompt'])
        return result['task']

    def result(self, task, output=None, issues=None):
        result = {**task['result_template'], 'summary': '已经完成本轮审查，以下结论等待作者核对。', 'issues': issues or []}
        if output:
            result['output'] = output
        return result

    def expect(self, code, call):
        with self.assertRaises(story.StoryError) as raised:
            call()
        self.assertEqual(raised.exception.code, code, raised.exception.details)

    def test_handoff_commands_use_current_interpreter_and_shell_safe_arguments(self):
        arguments = ["/Applications/Writer's Tools/python", "C:/Author's workspace/story.py", 'task-return',
                     '--book', "C:/book's workspace/$not_a_variable/`not_code`", '--version', 1]
        shell, command = self.tasks._handoff_command(arguments, windows=False)
        self.assertEqual(shell, 'POSIX shell')
        self.assertEqual(shlex.split(command), [str(value) for value in arguments])
        shell, command = self.tasks._handoff_command(arguments, windows=True)
        self.assertEqual(shell, 'Windows PowerShell')
        self.assertTrue(command.startswith("& '/Applications/Writer''s Tools/python' "))
        self.assertIn("'C:/Author''s workspace/story.py'", command)
        self.assertIn("'C:/book''s workspace/$not_a_variable/`not_code`'", command)
        task = self.task()
        expected_shell, prefix = self.tasks._handoff_command([sys.executable], windows=os.name == 'nt')
        self.assertIn('回填命令（' + expected_shell + '）：\n' + prefix, task['prompt'])

    def test_empty_list_does_not_create_workflow_files(self):
        before = self.fixture.authoritative_state()
        self.assertEqual(self.tasks.list_tasks(self.root)['tasks'], [])
        self.assertFalse((self.root / self.tasks.DIRECTORY).exists())
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_large_task_catalog_pages_without_loading_other_records(self):
        task = self.task()
        directory = self.root / self.tasks.DIRECTORY
        original = directory / (task['id'] + '.json')
        template = original.read_text(encoding='utf-8')
        original.unlink()
        keys, written = [], 0
        for count in (2001, 4001):
            for index in range(written, count):
                ident = f'{index:032x}'
                path = directory / (ident + '.json')
                path.write_text(template.replace(task['id'], ident), encoding='utf-8')
                timestamp = 1700000000000000000 + (index % 13) * 1000000000
                os.utime(path, ns=(timestamp, timestamp))
                keys.append((path.stat().st_mtime_ns, ident))
            written = count
            ordered = [ident for _, ident in sorted(keys, reverse=True)]
            for offset, limit in ((0, 50), (1990, 20), (count - 3, 20), (count, 20)):
                with self.subTest(total=count, offset=offset, limit=limit):
                    read = self.tasks._record
                    push = self.tasks.heapq.heappush
                    peak = []
                    def remember(heap, item):
                        push(heap, item)
                        peak.append(len(heap))
                    with patch.object(self.tasks, '_record', wraps=read) as records, \
                            patch.object(self.tasks.heapq, 'heappush', side_effect=remember):
                        page = self.tasks.list_tasks(self.root, offset=offset, limit=limit)
                    expected = ordered[offset:offset + limit]
                    self.assertEqual(page['total'], count)
                    self.assertEqual(page['has_more'], offset + limit < count)
                    self.assertEqual([row['id'] for row in page['tasks']], expected)
                    self.assertEqual(records.call_count, len(expected))
                    self.assertEqual([call.args[1] for call in records.call_args_list], expected)
                    self.assertLessEqual(max(peak, default=0), offset + limit)
            self.expect('invalid_input', lambda: self.tasks.list_tasks(self.root, limit=51))

    def test_task_removed_during_catalog_scan_does_not_report_partial_total(self):
        task = self.task()
        class RemovedEntry:
            name = task['id'] + '.json'
            def stat(self, follow_symlinks=False):
                raise FileNotFoundError('Task moved during scan')
        with patch.object(self.tasks.os, 'scandir') as scan:
            scan.return_value.__enter__.return_value = iter([RemovedEntry()])
            self.expect('task_catalog_changed', lambda: self.tasks.list_tasks(self.root))

    def test_review_task_persists_restarts_and_does_not_modify_novel(self):
        before = self.fixture.authoritative_state()
        task = self.task()
        self.assertEqual(task['status'], 'generated')
        self.assertEqual(task['execution']['status'], 'unverified')
        self.assertIn('task-return', task['prompt'])
        self.assertEqual(self.tasks.get(self.root, task['id'])['task'], task)
        self.assertEqual(self.tasks.list_tasks(self.root)['tasks'][0]['id'], task['id'])
        self.assertEqual(before, self.fixture.authoritative_state())

    def test_handoff_then_cli_return_accepts_original_prompt_version_once(self):
        task = self.task()
        handoff = self.tasks.mark_handoff(self.root, task['id'], task['version'])['task']
        self.assertEqual(handoff['status'], 'handed_off')
        result = self.result(task)
        returned = self.tasks.import_result(self.root, task['id'], task['version'], result, source='local_cli')['task']
        self.assertEqual(returned['status'], 'needs_review')
        self.assertEqual(returned['execution']['status'], 'unverified')
        self.assertEqual(returned['execution']['source'], 'local_cli')
        self.assertTrue(self.tasks.import_result(self.root, task['id'], 1, result)['unchanged'])
        changed = {**result, 'summary': '不能覆盖已有意见。'}
        self.expect('task_result_conflict', lambda: self.tasks.import_result(self.root, task['id'], returned['version'], changed))

    def test_late_return_cannot_erase_failure_without_reading_current_version(self):
        task = self.task()
        failed = self.tasks.mark_failed(self.root, task['id'], task['version'], '本轮执行未完成，等待重新核对。')['task']
        result = self.result(task)
        self.expect('task_version_changed', lambda: self.tasks.import_result(self.root, task['id'], 1, result))
        saved = self.tasks.get(self.root, task['id'])['task']
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(saved['failure'], failed['failure'])
        recovered = self.tasks.import_result(self.root, task['id'], failed['version'], result)['task']
        self.assertEqual(recovered['status'], 'needs_review')
        self.assertEqual(recovered['failure'], failed['failure'])

    def test_mismatched_book_binding_and_spoofed_execution_are_rejected(self):
        task = self.task()
        for field, value in [('book_id', 'other-book'), ('binding_sha256', '0' * 64),
                             ('task_id', 'f' * 32), ('execution', {'status': 'verified'})]:
            with self.subTest(field=field):
                result = {**self.result(task), field: value}
                self.expect('task_result_mismatch', lambda: self.tasks.import_result(self.root, task['id'], 1, result))
        self.assertEqual(self.tasks.get(self.root, task['id'])['task']['status'], 'generated')

    def test_source_external_change_keeps_late_result_but_blocks_confirmation(self):
        source = self.material('.story/drafts/审查对象.md', '她只答应借一把钥匙。\n')
        task = story.workbench._editor_review_task(self.root, 'file:' + source['path'])['task']
        self.material(source['path'], '她现在明确拒绝借钥匙。\n')
        returned = self.tasks.import_result(self.root, task['id'], 1, self.result(task))['task']
        self.assertEqual(returned['status'], 'needs_review')
        self.assertTrue(returned['stale_reasons'])
        self.assertFalse(returned['can_review'])
        self.expect('stale_snapshot', lambda: self.tasks.review(self.root, task['id'], returned['version']))

    def test_formal_export_modified_outside_database_is_stale(self):
        task = self.task()
        path = self.root / self.book.chapter_path(2)
        path.write_text(path.read_text() + '这是未采用的外部变化。\n', encoding='utf-8')
        current = self.tasks.get(self.root, task['id'])['task']
        self.assertTrue(current['stale_reasons'])
        self.expect('stale_snapshot', lambda: self.tasks.mark_handoff(self.root, task['id'], 1))

    def test_candidate_result_changed_after_return_cannot_be_reviewed(self):
        task = self.task()
        output = self.material('.story/drafts/第2章_返回候选.md', '第2章 返回候选\n沈禾留下钥匙。\n')
        returned = self.tasks.import_result(self.root, task['id'], 1, self.result(task, output))['task']
        self.material(output['path'], '第2章 返回候选\n换成了另一份候选。\n')
        self.expect('stale_snapshot', lambda: self.tasks.review(self.root, task['id'], returned['version']))

    def test_cross_chapter_evidence_opens_exact_versions_and_tracks_resolution(self):
        task = self.task()
        output = self.material('.story/drafts/第2章_返回候选.md', '第2章 返回候选\n沈禾把钥匙留给守门人。\n')
        previous = self.book.db.execute('SELECT sha FROM chapter_state WHERE chapter=1').fetchone()[0]
        issues = [{'id': 'continuity-1', 'severity': 'blocker', 'issue': '请核对钥匙交接的重复表述。',
                   'evidence': [{'chapter': 1, 'sha256': previous, 'quote': '沈禾把唯一的钥匙交给守门人。'},
                                {**output, 'quote': '沈禾把钥匙留给守门人。', 'line': 2}]}]
        returned = self.tasks.import_result(self.root, task['id'], 1, self.result(task, output, issues))['task']
        evidence = self.tasks.evidence(self.root, task['id'], 'continuity-1', 1)
        self.assertEqual(evidence['text'][evidence['start']:evidence['end']], issues[0]['evidence'][1]['quote'])
        self.assertEqual(evidence['document_id'], 'file:' + output['path'])
        unresolved = self.tasks.review(self.root, task['id'], returned['version'])['task']
        self.assertEqual(unresolved['status'], 'needs_review')
        reviewed = self.tasks.review(self.root, task['id'], unresolved['version'],
            [{'id': 'continuity-1', 'status': 'dismissed', 'note': '已核对是对上章事件的回忆，保留前后原文。'}])['task']
        self.assertEqual(reviewed['status'], 'reviewed')
        self.assertIsNone(reviewed['applied'])

    def test_changed_evidence_shows_original_snapshot_not_replacement_text(self):
        task = self.task()
        output = self.material('.story/drafts/第2章_证据.md', '第2章 证据\n她把钥匙留给守门人。\n')
        issue = {'id': 'one', 'severity': 'advice', 'issue': '核对交接动作。',
                 'evidence': [{**output, 'quote': '她把钥匙留给守门人。'}]}
        returned = self.tasks.import_result(self.root, task['id'], 1, self.result(task, output, [issue]))['task']
        self.material(output['path'], '第2章 证据\n替换后的新稿没有交接动作。\n')
        evidence = self.tasks.evidence(self.root, task['id'], 'one', 0)
        self.assertTrue(evidence['stale'])
        self.assertIsNone(evidence['document_id'])
        self.assertIn('她把钥匙留给守门人。', evidence['text'])
        self.assertNotIn('替换后的新稿', evidence['text'])
        self.expect('stale_snapshot', lambda: self.tasks.review(self.root, task['id'], returned['version']))

    def test_ambiguous_or_wrong_quote_requires_accurate_line(self):
        task = self.task()
        output = self.material('.story/drafts/重复.md', '第2章 重复\n她没有走。\n她没有走。\n')
        issue = {'id': 'one', 'severity': 'advice', 'issue': '检查重复是否有作用。',
                 'evidence': [{**output, 'quote': '她没有走。'}]}
        self.expect('invalid_evidence', lambda: self.tasks.import_result(self.root, task['id'], 1,
            self.result(task, output, [issue])))
        issue['evidence'][0]['line'] = 3
        returned = self.tasks.import_result(self.root, task['id'], 1, self.result(task, output, [issue]))['task']
        self.assertEqual(returned['issues'][0]['evidence'][0]['line'], 3)

    def test_arbitrary_internal_files_formal_paths_and_escape_are_not_outputs(self):
        task = self.task()
        for path in ('../other.md', '/tmp/other.md', '.story/state.sqlite3', self.book.chapter_path(2),
                     '.story/drafts/../state.sqlite3', '.story/drafts/result.json'):
            with self.subTest(path=path):
                with self.assertRaises(story.StoryError):
                    self.tasks.import_result(self.root, task['id'], 1, self.result(task, {'path': path, 'sha256': '0' * 64}))

    def test_wrong_chapter_and_material_candidate_cannot_be_prose_output(self):
        task = self.task()
        output = self.material('.story/drafts/第3章_别章.md', '第3章 别章\n这是另一章。\n')
        self.expect('task_output_scope', lambda: self.tasks.import_result(self.root, task['id'], 1, self.result(task, output)))
        output = self.material('.story/drafts/workbench/第2章_材料.md', '第2章 材料\n只是材料引用。\n')
        self.material(output['path'] + '.meta.json', json.dumps({'content_kind': 'material'}))
        self.expect('task_output_scope', lambda: self.tasks.import_result(self.root, task['id'], 1, self.result(task, output)))

    def test_chapter_linked_fragment_return_scope_for_api_and_cli(self):
        for route in ('api', 'cli'):
            with self.subTest(route=route):
                task = self.tasks.create(self.root, 'writing', '只写第3章的一段对白。', [],
                                         chapter=3, complete_chapter=False)['task']
                record = self.root / self.tasks.DIRECTORY / (task['id'] + '.json')
                original = record.read_bytes()

                def submit(output):
                    result = self.result(task, output)
                    if route == 'api':
                        try:
                            return None, self.tasks.import_result(self.root, task['id'], 1, result)
                        except story.StoryError as error:
                            return error.code, None
                    result_path = '.story/drafts/fragment-return-' + route + '.json'
                    self.material(result_path, json.dumps(result, ensure_ascii=False))
                    command = [sys.executable, '-B', str(base.TOOL), 'task-return', '--book', str(self.root),
                               '--task-id', task['id'], '--version', '1', '--result-file', result_path]
                    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    packet = json.loads(completed.stdout or completed.stderr)
                    return packet.get('error') if completed.returncode else None, packet

                for name, text, meta in (
                    ('wrong-heading', '第4章 另一章\n她说了句话。\n', None),
                    ('wrong-heading-no-title', '第9章\n她说了句话。\n', None),
                    ('wrong-heading-markdown', '# 第9章\n她说了句话。\n', None),
                    ('wrong-heading-leading-blank', '\n \n第9章\n片段', None),
                    ('wrong-heading-markdown-leading-blank', '\n# 第9章\n片段', None),
                    ('wrong-metadata', '她说了句话。\n', {'chapter': 4}),
                    ('material', '她说了句话。\n', {'content_kind': 'material'}),
                ):
                    output = self.material(f'.story/drafts/workbench/{route}-{name}.md', text)
                    if meta is not None:
                        self.material(output['path'] + '.meta.json', json.dumps(meta))
                    error, _ = submit(output)
                    self.assertEqual(error, 'task_output_scope')
                    self.assertEqual(record.read_bytes(), original)
                for name, text in (('empty', ''), ('blank', ' \n\t\u200b\u2060')):
                    output = self.material(f'.story/drafts/workbench/{route}-{name}.md', text)
                    error, _ = submit(output)
                    self.assertEqual(error, 'task_output_incomplete')
                    self.assertEqual(record.read_bytes(), original)
                output = self.material(f'.story/drafts/workbench/{route}-fragment.md', '她说了句话。\n')
                error, _ = submit(output)
                self.assertIsNone(error)
                self.assertEqual(self.tasks.get(self.root, task['id'])['task']['status'], 'needs_review')

    def test_complete_and_legacy_chapter_tasks_still_require_heading(self):
        output = self.material('.story/drafts/headingless.md', '她说了句话。\n')
        complete = self.tasks.create(self.root, 'writing', '写完整章。', [], chapter=2,
                                     complete_chapter=True)['task']
        legacy = self.tasks.create(self.root, 'writing', '旧的章关联任务。', [], chapter=2)['task']
        path = self.root / self.tasks.DIRECTORY / (legacy['id'] + '.json')
        saved = json.loads(path.read_text(encoding='utf-8'))
        saved['binding'].pop('complete_chapter')
        previous = saved['binding_sha256']
        saved['binding_sha256'] = story.digest(story.dumps(saved['binding']))
        saved['prompt'] = saved['prompt'].replace(previous, saved['binding_sha256'])
        path.write_text(story.dumps(saved), encoding='utf-8')
        legacy = self.tasks.get(self.root, legacy['id'])['task']
        for task in (complete, legacy):
            with self.subTest(task=task['id']):
                record = self.root / self.tasks.DIRECTORY / (task['id'] + '.json')
                before = record.read_bytes()
                self.expect('task_output_scope', lambda: self.tasks.import_result(
                    self.root, task['id'], task['version'], self.result(task, output)))
                self.assertEqual(record.read_bytes(), before)

    def test_symlink_and_hardlink_sources_and_records_are_rejected(self):
        task = self.task()
        target = self.root / '.story/drafts/target.md'
        target.write_text('原文', encoding='utf-8')
        link = target.with_name('hard.md')
        os.link(target, link)
        self.addCleanup(lambda: link.unlink(missing_ok=True))
        self.expect('unsafe_publish_path', lambda: self.tasks.import_result(self.root, task['id'], 1,
            self.result(task, {'path': '.story/drafts/hard.md', 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})))
        record = self.root / self.tasks.DIRECTORY / (task['id'] + '.json')
        copied = record.with_name('record-alias')
        os.link(record, copied)
        self.expect('unsafe_publish_path', lambda: self.tasks.get(self.root, task['id']))

    def test_result_file_reader_only_accepts_bounded_book_local_json(self):
        task = self.task()
        path = '.story/drafts/receipt.json'
        self.material(path, json.dumps(self.result(task), ensure_ascii=False))
        self.assertEqual(self.tasks.read_result_file(self.root, path), self.result(task))
        self.expect('invalid_input', lambda: self.tasks.read_result_file(self.root, '.story/workbench-service.json'))
        self.material(path, '{broken')
        self.expect('invalid_input', lambda: self.tasks.read_result_file(self.root, path))

    def test_wrong_book_record_copy_and_expected_identity_fail(self):
        task = self.task()
        other = self.root.parent / '另一作品'
        story.Book.create(other, '另一作品', 'long')
        destination = other / self.tasks.DIRECTORY
        destination.mkdir()
        shutil.copy2(self.root / self.tasks.DIRECTORY / (task['id'] + '.json'), destination)
        self.expect('task_record_invalid', lambda: self.tasks.get(other, task['id']))
        self.expect('wrong_book', lambda: self.tasks.get(self.root, task['id'], expected_book_id='wrong'))

    def test_corrupt_scope_and_execution_claims_are_not_rendered(self):
        task = self.task()
        path = self.root / self.tasks.DIRECTORY / (task['id'] + '.json')
        original = json.loads(path.read_bytes())
        variants = []
        scope = copy.deepcopy(original)
        scope['binding']['revision'] = 'invented'
        scope['binding_sha256'] = story.digest(story.dumps(scope['binding']))
        variants.append(scope)
        execution = copy.deepcopy(original)
        execution['execution'] = {'status': 'verified', 'source': 'native_run'}
        variants.append(execution)
        result = copy.deepcopy(original)
        result['state'] = 'reviewed'
        result['result'] = []
        variants.append(result)
        for value in variants:
            with self.subTest(value=value):
                path.write_text(story.dumps(value), encoding='utf-8')
                self.expect('task_record_invalid', lambda: self.tasks.get(self.root, task['id']))

    def test_tampered_original_snapshot_never_substitutes_for_evidence(self):
        task = self.task()
        output = self.material('.story/drafts/第2章_快照.md', '第2章 快照\n她收起了钥匙。\n')
        issue = {'id': 'one', 'severity': 'advice', 'issue': '检查动作。',
                 'evidence': [{**output, 'quote': '她收起了钥匙。'}]}
        self.tasks.import_result(self.root, task['id'], 1, self.result(task, output, [issue]))
        self.material(output['path'], '第2章 快照\n新稿已经改变。\n')
        snapshot = self.root / self.tasks.DIRECTORY / 'objects' / (output['sha256'] + '.txt')
        snapshot.write_text('假冒的历史原文', encoding='utf-8')
        self.expect('task_record_invalid', lambda: self.tasks.evidence(self.root, task['id'], 'one', 0))

    def test_source_parent_link_swap_cannot_publish_to_replacement_book(self):
        if os.name == 'nt':
            self.skipTest('POSIX directory replacement fault injection')
        task = self.task()
        original = self.tasks._save
        moved = self.root.parent / '已移动原作品'
        other = self.root.parent / '另一作品'
        story.Book.create(other, '另一作品', 'long')
        before = (other / '.story/state.sqlite3').read_bytes()

        def replacing(book, record, previous_sha=None):
            self.root.rename(moved)
            self.root.symlink_to(other, target_is_directory=True)
            return original(book, record, previous_sha)

        with patch.object(self.tasks, '_save', side_effect=replacing):
            with self.assertRaises((story.StoryError, OSError)):
                self.tasks.mark_handoff(self.root, task['id'], 1)
        self.assertEqual((other / '.story/state.sqlite3').read_bytes(), before)
        self.assertFalse((other / self.tasks.DIRECTORY).exists())

    def test_directory_replacement_at_publication_cannot_write_new_book(self):
        if os.name == 'nt':
            self.skipTest('POSIX directory replacement fault injection')
        task = self.task()
        original = story._publish_no_replace
        moved = self.root.parent / '原书已移动'
        other = self.root.parent / '替换书'
        story.Book.create(other, '替换书', 'long')
        before = (other / '.story/state.sqlite3').read_bytes()

        def replacing(source, target):
            self.root.rename(moved)
            other.rename(self.root)
            return original(source, target)

        with patch.object(self.tasks.api, '_publish_no_replace', side_effect=replacing):
            with self.assertRaises((story.StoryError, OSError)):
                self.tasks.mark_handoff(self.root, task['id'], 1)
        self.assertEqual((self.root / '.story/state.sqlite3').read_bytes(), before)
        self.assertFalse((self.root / self.tasks.DIRECTORY).exists())

    def test_concurrent_cli_returns_keep_one_result_and_never_overwrite(self):
        task = self.task()
        jobs = []
        for index in range(2):
            result = {**self.result(task), 'summary': f'第{index}份独立返回。'}
            path = f'.story/drafts/return-{index}.json'
            self.material(path, json.dumps(result, ensure_ascii=False))
            command = [sys.executable, '-B', str(base.TOOL), 'task-return', '--book', str(self.root),
                       '--task-id', task['id'], '--version', '1', '--result-file', path]
            jobs.append(subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        results = []
        for job in jobs:
            stdout, stderr = job.communicate(timeout=30)
            results.append((job.returncode, json.loads(stdout or stderr)))
        self.assertEqual(sorted(code for code, _ in results), [0, 2], results)
        failure = next(value for code, value in results if code)
        self.assertEqual(failure['error'], 'task_result_conflict')
        final = self.tasks.get(self.root, task['id'])['task']
        self.assertEqual(final['version'], 2)
        self.assertEqual(final['status'], 'needs_review')
        self.assertEqual(final['execution']['source'], 'local_cli')

    def test_http_return_get_evidence_and_review_uses_registered_book(self):
        server = story.workbench.editor_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.shutdown)
        receipt = json.loads((self.root / '.story/workbench-service.json').read_bytes())
        route = '/' + server.editor_url.rstrip('/').split('/')[-1] + '/api/'

        def post(action, value):
            client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                body = json.dumps(value).encode()
                client.request('POST', route + action, body, {'Content-Type': 'application/json',
                    'Origin': f'http://127.0.0.1:{server.server_port}', 'X-Story-Token': receipt['token']})
                response = client.getresponse()
                return response.status, json.loads(response.read())
            finally:
                client.close()

        status, created = post('review-task', {'id': 'formal:2'})
        self.assertEqual(status, 200, created)
        task = created['task']
        status, returned = post('tasks/import', {'task_id': task['id'], 'version': 1, 'result': self.result(task)})
        self.assertEqual(status, 200, returned)
        status, reviewed = post('tasks/review', {'task_id': task['id'], 'version': returned['task']['version']})
        self.assertEqual(status, 200, reviewed)
        self.assertEqual(reviewed['task']['status'], 'reviewed')
        status, listing = post('tasks/list', {})
        self.assertEqual(status, 200, listing)
        self.assertEqual(listing['tasks'][0]['id'], task['id'])
        status, denied = post('tasks/get', {'task_id': task['id'], 'book_id': 'replace'})
        self.assertEqual(status, 409, denied)

    def test_writing_handoff_includes_request_style_and_previous_prose(self):
        plan = copy.deepcopy(self.book.get_plan(2))
        plan['title'] = '第三次敲门'
        self.book.save_plan(3, plan, self.book.meta('revision'))
        bind_adopted_outline(story, self.book, 3)
        request = self.material('本次请求.md', '只写第三章，不提前揭示谁藏了账本。')
        style = self.material('文风.md', '保留人物自然应答和有用的安静。')
        result = story.workbench._editor_writing_task(self.root, request['path'], style['path'])
        self.assertEqual(result['brief']['current_request']['sha256'], request['sha256'])
        sources = result['task']['input_sources']
        self.assertIn(request, sources)
        self.assertIn(style, sources)
        self.assertTrue(any(v.get('chapter') == 2 for v in sources))

    def test_old_task_versions_do_not_overwrite_review_decisions(self):
        task = self.task()
        returned = self.tasks.import_result(self.root, task['id'], 1, self.result(task))['task']
        self.expect('task_version_changed', lambda: self.tasks.review(self.root, task['id'], 1))
        reviewed = self.tasks.review(self.root, task['id'], returned['version'])['task']
        self.assertEqual(reviewed['status'], 'reviewed')
        self.expect('task_state_conflict', lambda: self.tasks.mark_failed(self.root, task['id'], reviewed['version'], '迟到的失败说明'))

    def test_applied_requires_actual_formal_state_not_reported_status(self):
        task = self.task()
        text = (self.root / self.book.chapter_path(2)).read_text(encoding='utf-8') + '她终于看见了侧门的灯。\n'
        output = self.material('.story/drafts/第2章_待采用.md', text)
        result = self.tasks.import_result(self.root, task['id'], 1, self.result(task, output))['task']
        reviewed = self.tasks.review(self.root, task['id'], result['version'])['task']
        self.assertEqual(reviewed['status'], 'reviewed')
        delta = {'book_id': self.book.meta('id'), 'base_revision': self.book.meta('revision'),
                 'summary': '交接后看到侧门灯光。', 'changes': [], 'review': {
                     'draft_sha256': story.digest(text), 'checks': {key: {'note': '核对交接与侧门灯光。',
                         'quote': '她终于看见了侧门的灯。'} for key in story.CHECKS}, 'issues': []}}
        self.book.commit(2, self.root / output['path'], delta, replace_last=True)
        applied = self.tasks.get(self.root, task['id'])['task']
        self.assertEqual(applied['status'], 'applied')
        self.assertEqual(applied['applied']['source'], 'current_formal_state')
        # A later external edit of either side invalidates the derived claim.
        formal = self.root / self.book.chapter_path(2)
        formal.write_text(text + '尚未采用的外部改字。\n', encoding='utf-8')
        self.assertEqual(self.tasks.get(self.root, task['id'])['task']['status'], 'reviewed')
        formal.write_text(text, encoding='utf-8')
        self.assertEqual(self.tasks.get(self.root, task['id'])['task']['status'], 'applied')
        self.material(output['path'], text + '候选文件后续改动。\n')
        self.assertEqual(self.tasks.get(self.root, task['id'])['task']['status'], 'reviewed')

    def test_replaced_formal_chapter_evidence_keeps_original_body_and_marks_stale(self):
        task = self.task()
        original_sha = self.book.db.execute('SELECT sha FROM chapter_state WHERE chapter=2').fetchone()[0]
        original_text = self.book.db.execute('SELECT text FROM core_objects WHERE sha=?', (original_sha,)).fetchone()[0]
        quote = next(line for line in original_text.splitlines()[1:] if line.strip())
        issue = {'id': 'prior', 'severity': 'blocker', 'issue': '核对之前版本的原句。',
                 'evidence': [{'chapter': 2, 'sha256': original_sha, 'quote': quote}]}
        returned = self.tasks.import_result(self.root, task['id'], 1, self.result(task, issues=[issue]))['task']
        replacement = original_text + '她终于看见了侧门的灯。\n'
        output = self.material('.story/drafts/第2章_历史证据替换.md', replacement)
        delta = {'book_id': self.book.meta('id'), 'base_revision': self.book.meta('revision'),
                 'summary': '交接后看到侧门灯光。', 'changes': [], 'review': {
                     'draft_sha256': story.digest(replacement), 'checks': {key: {'note': '核对交接与侧门灯光。',
                         'quote': '她终于看见了侧门的灯。'} for key in story.CHECKS}, 'issues': []}}
        self.book.commit(2, self.root / output['path'], delta, replace_last=True)
        proof = self.tasks.evidence(self.root, task['id'], 'prior', 0)
        self.assertEqual(proof['text'], original_text)
        self.assertEqual(proof['source_sha256'], original_sha)
        self.assertTrue(proof['stale'])
        self.assertIsNone(proof['document_id'])
        self.expect('stale_snapshot', lambda: self.tasks.review(self.root, task['id'], returned['version']))


if __name__ == '__main__':
    unittest.main()
