"""Version-bound assistant handoffs and review evidence; never adopts manuscripts."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import heapq
import json
import os
from pathlib import Path
import re
import shlex
import sys
from types import SimpleNamespace
import uuid


DIRECTORY = '.story/assistant-tasks'
MAX_RECORD = 512 * 1024
MAX_SOURCE = 1024 * 1024
STATES = {'generated', 'handed_off', 'failed', 'needs_review', 'reviewed'}
LABELS = {'generated': '任务已生成', 'handed_off': '已交给助手，执行尚未核验',
          'failed': '已记录失败', 'needs_review': '结果已回流，待核对',
          'reviewed': '已核对，尚未确认正式采用', 'applied': '已核对正式采用'}


def inject(core):
    global api
    api = core


def _now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        api.fail('invalid_input', '助手任务标识无效。')
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        api.fail('invalid_input', '请提供完整 SHA-256 校验值。')
    return value


def _relative(value):
    if (not isinstance(value, str) or not value or len(value) > 2048 or
            '\\' in value or ':' in value or value != value.strip() or
            any(ord(c) < 32 for c in value) or
            any(p in ('', '.', '..') for p in value.split('/')) or Path(value).is_absolute()):
        api.fail('invalid_input', '请使用本书内的规范相对文件路径。')
    return value


def _read(root, relative, maximum=MAX_RECORD):
    """Read one stable regular file through a pinned parent, rejecting aliases."""
    path = api.safe_path(root, _relative(relative))
    with api._pinned_read_directory(path.parent) as directory:
        target = api._BoundFile(directory, path.name)
        before = api.publish._bound_stat(target)
        if before.st_nlink != 1 or before.st_size > maximum:
            api.fail('task_source_unsafe', '任务来源含硬链接或超过读取上限。', path=relative)
        with api._bound_reader(target) as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read(maximum + 1)
            after_read = os.fstat(stream.fileno())
        after = api.publish._bound_stat(target)
        if (not os.path.samestat(before, opened) or not os.path.samestat(before, after)
                or opened.st_nlink != 1 or len(raw) > maximum
                or before.st_size != len(raw) or before.st_mtime_ns != after.st_mtime_ns
                or opened.st_mtime_ns != after_read.st_mtime_ns):
            api.fail('task_source_changed', '任务来源在读取期间变化，请刷新。', path=relative)
        api._verify_bound_directory(directory)
    return raw


@contextmanager
def _book(root, expected_book_id=None, write=False):
    book = api.workbench._ReadOnlyBook(Path(root).expanduser().absolute())
    try:
        if expected_book_id is not None and book.meta('id') != expected_book_id:
            api.fail('wrong_book', '任务与当前作品身份不一致。')
        if write:
            with api.storage.operation_lock(book):
                _verify_book(book)
                with book.read_snapshot():
                    yield book
                _verify_book(book)
        else:
            with book.read_snapshot():
                yield book
            _verify_book(book)
    finally:
        book.close()


def _verify_book(book):
    book.verify_state_path()
    with api._pinned_read_directory(book.path.parent) as directory:
        visible = api.publish._bound_stat(api._BoundFile(directory, book.path.name))
    if not os.path.samestat(book._state_stat, visible):
        api.fail('wrong_book', '作品目录或状态文件已经替换，请重新打开。')


def _record(book, task_id):
    raw = _read(book.root, DIRECTORY + '/' + _id(task_id) + '.json')
    try:
        record = json.loads(raw)
        if (not isinstance(record, dict) or type(record.get('schema')) is not int or record['schema'] != 1 or
                record.get('id') != task_id or record.get('book_id') != book.meta('id') or
                record.get('state') not in STATES or type(record.get('version')) is not int or
                record['version'] < 1 or not isinstance(record.get('binding'), dict) or
                api.digest(api.dumps(record['binding'])) != record.get('binding_sha256')):
            raise ValueError('invalid task identity or binding')
        binding = record['binding']
        binding_keys = {'kind', 'book_id', 'revision', 'chapter', 'sources', 'initial_formal_sha256'}
        if (set(binding) not in (binding_keys, binding_keys | {'complete_chapter'}) or
                binding['kind'] not in ('review', 'writing') or binding['book_id'] != record['book_id'] or
                type(binding['revision']) is not int or binding['revision'] < 0 or
                not isinstance(binding['sources'], list) or len(binding['sources']) > 33 or
                not isinstance(record.get('prompt'), str) or len(record['prompt']) > 160000):
            raise ValueError('invalid task scope')
        if 'complete_chapter' in binding and (binding['kind'] != 'writing'
                or type(binding['complete_chapter']) is not bool
                or (binding['complete_chapter'] and binding['chapter'] is None)):
            raise ValueError('invalid complete-chapter scope')
        if binding['chapter'] is not None:
            api.workbench._editor_chapter(binding['chapter'])
        if binding['initial_formal_sha256'] is not None:
            _sha(binding['initial_formal_sha256'])
        for name in ('created', 'updated'):
            if not isinstance(record.get(name), str) or not 1 <= len(record[name]) <= 80:
                raise ValueError('invalid task time')
        execution = record.get('execution')
        if (not isinstance(execution, dict) or execution.get('status') != 'unverified' or
                execution.get('source') not in ('none', 'author_handoff', 'author_report', 'manual_import', 'local_cli')):
            raise ValueError('unsupported execution claim')
        refs = list(binding['sources'])
        result = record.get('result')
        if result is not None:
            if (not isinstance(result, dict) or result.get('task_id') != record['id'] or
                    result.get('book_id') != record['book_id'] or result.get('binding_sha256') != record['binding_sha256'] or
                    not isinstance(result.get('summary'), str) or not 1 <= len(result['summary']) <= 4000 or
                    not isinstance(result.get('issues'), list) or len(result['issues']) > 60):
                raise ValueError('invalid stored result')
            if result.get('output'):
                refs.append(result['output'])
            identifiers = set()
            for issue in result['issues']:
                if (not isinstance(issue, dict) or not isinstance(issue.get('id'), str) or
                        not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', issue['id']) or issue['id'] in identifiers or
                        issue.get('severity') not in ('blocker', 'advice') or
                        not isinstance(issue.get('issue'), str) or not 1 <= len(issue['issue']) <= 2000 or
                        not isinstance(issue.get('evidence'), list) or not 1 <= len(issue['evidence']) <= 6):
                    raise ValueError('invalid stored issue')
                identifiers.add(issue['id'])
                refs.extend(issue['evidence'])
            statuses = record.get('issue_statuses')
            if not isinstance(statuses, list) or len(statuses) != len(identifiers):
                raise ValueError('invalid issue decisions')
            seen = set()
            for value in statuses:
                if (not isinstance(value, dict) or value.get('id') not in identifiers or value['id'] in seen or
                        value.get('status') not in ('open', 'resolved', 'dismissed') or
                        not isinstance(value.get('note'), str) or len(value['note']) > 1200):
                    raise ValueError('invalid issue decision')
                seen.add(value['id'])
        elif record['state'] in ('needs_review', 'reviewed'):
            raise ValueError('result missing for reviewed state')
        for ref in refs:
            if (not isinstance(ref, dict) or set(ref) - {'path', 'chapter', 'sha256', 'quote', 'line'} or
                    ('path' in ref) == ('chapter' in ref)):
                raise ValueError('invalid stored reference')
            _sha(ref.get('sha256'))
            if 'path' in ref:
                _relative(ref['path'])
            else:
                api.workbench._editor_chapter(ref['chapter'])
            if 'quote' in ref:
                api.text_field(ref['quote'], 'quote', 2000)
                api.integer(ref.get('line'), 'line', 1)
    except (api.StoryError, ValueError, KeyError, TypeError, RecursionError, UnicodeError) as error:
        api.fail('task_record_invalid', '助手任务记录损坏或不属于此书，请保留记录后核对。', reason=str(error))
    return record, hashlib.sha256(raw).hexdigest()


def _save(book, record, previous_sha=None):
    content = api.dumps(record)
    if len(content.encode('utf-8')) > MAX_RECORD:
        api.fail('task_record_too_large', '任务记录超过上限，请减少交接或意见内容。')
    _verify_book(book)
    name = record['id'] + '.json'
    _publish(book, (), name, content, previous_sha)
    _verify_book(book)


@contextmanager
def _task_directory(book, parts=()):
    """Descend from the already checked .story descriptor, not a new book path."""
    path = book.root / DIRECTORY
    for part in parts:
        path /= part
    if os.name == 'nt':
        with api._pinned_directory(path, create=True) as directory:
            _verify_book(book)
            yield directory
        return
    handles = []
    current = book._state_file.directory.fd
    try:
        for part in ('assistant-tasks',) + tuple(parts):
            try:
                os.mkdir(part, 0o700, dir_fd=current)
            except FileExistsError:
                pass
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            handles.append(current)
        yield SimpleNamespace(path=path, fd=current)
    finally:
        for handle in reversed(handles):
            os.close(handle)


def _publish(book, parts, name, content, previous_sha=None, *, retain_backup=True):
    """CAS on bound task files; a renamed book cannot redirect a late write."""
    with _task_directory(book, parts) as directory:
        target = api._BoundFile(directory, name)
        backup = api._BoundFile(directory, '.previous-' + uuid.uuid4().hex + '-' + name)
        fd, staged = api._bound_stage(directory, '.stage-')
        displaced = False
        try:
            with api._owned_fdopen(fd, 'wb') as stream:
                stream.write(content.encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            _verify_book(book)
            api._verify_bound_directory(directory)
            if api._bound_exists(target):
                api.publish._bound_stat(target)  # rejects links and special files
                if previous_sha is None:
                    api.fail('task_version_changed', '任务文件已经存在，未覆盖并发结果。')
                api._bound_replace(target, backup)
                displaced = True
                if api._bound_hash(backup) != previous_sha:
                    api.fail('task_version_changed', '任务文件在保存期间变化，旧记录已保留。')
            elif previous_sha is not None:
                api.fail('task_version_changed', '待更新的任务文件已移走，请重新读取。')
            api._publish_no_replace(staged, target)
            if api._bound_hash(target) != api.digest(content):
                api.fail('task_version_changed', '任务保存后内容不一致，请核对保留的记录。')
            _verify_book(book)
            api._verify_bound_directory(directory)
            if displaced and not retain_backup:
                # Heartbeats replace derived state, rather than creating an
                # unbounded recovery history of identical execution attempts.
                try:
                    api._bound_unlink(backup)
                except OSError:
                    pass
                displaced = False
        except (api.StoryError, OSError):
            if displaced:
                try:
                    api._restore_displaced_file(backup, target)
                    api._bound_unlink(backup)
                except (api.StoryError, OSError):
                    pass
            raise
        finally:
            if api._bound_exists(staged):
                api._bound_unlink(staged)


def _source_snapshot(book, source):
    if 'path' not in source:
        return
    raw = _read(book.root, source['path'], MAX_SOURCE)
    if hashlib.sha256(raw).hexdigest() != source['sha256']:
        api.fail('task_source_changed', '来源在保存任务前变化，请重新核对。')
    name = DIRECTORY + '/objects/' + source['sha256'] + '.txt'
    try:
        old = _read(book.root, name, MAX_SOURCE)
    except FileNotFoundError:
        _publish(book, ('objects',), source['sha256'] + '.txt', raw.decode('utf-8'))
    else:
        if old != raw:
            api.fail('task_record_invalid', '任务原文快照与校验值不匹配。')


def _reference(book, source, quoted=False, historical=False):
    if not isinstance(source, dict) or set(source) - {'chapter', 'path', 'sha256', 'quote', 'line'}:
        api.fail('invalid_input', '证据只接受章号或材料路径、校验值、引文及行号。')
    if ('chapter' in source) == ('path' in source):
        api.fail('invalid_input', '证据必须且只能指定正式章号或材料路径。')
    sha = _sha(source.get('sha256'))
    if 'chapter' in source:
        chapter = api.workbench._editor_chapter(source['chapter'])
        row = book.db.execute('SELECT sha FROM chapter_state WHERE chapter=?', (chapter,)).fetchone()
        current_sha = row[0] if row else None
        allowed = book.db.execute('SELECT sha FROM chapter_state WHERE chapter=? AND sha=? UNION '
            'SELECT sha FROM history_versions WHERE chapter=? AND sha=? LIMIT 1',
            (chapter, sha, chapter, sha)).fetchone()
        if not allowed:
            api.fail('task_evidence_missing', '此书没有对应的正式章节版本。', chapter=chapter)
        text = book.db.execute('SELECT text FROM core_objects WHERE sha=?', (sha,)).fetchone()[0]
        ref = {'chapter': chapter, 'sha256': sha}
        document_id = f'formal:{chapter}' if current_sha == sha else None
        metadata = {'__root': str(book.root)}
        stored = book.db.execute('SELECT value FROM meta WHERE key=?', (f'chapter_path:{chapter}',)).fetchone()
        if stored:
            metadata[f'chapter_path:{chapter}'] = json.loads(stored[0])
        path = api.workbench._chapter_path(metadata, chapter)
        try:
            export_sha = hashlib.sha256(_read(book.root, path, MAX_SOURCE)).hexdigest()
        except (api.StoryError, OSError):
            export_sha = None
        export_changed = export_sha != current_sha
        if export_changed:
            document_id = None
    else:
        path = _relative(source['path'])
        try:
            if api.workbench._author_file(book.root, 'file:' + path) is None:
                api.fail('invalid_input', '证据必须来自可浏览的本书正文、候选或材料。')
            raw = _read(book.root, path, MAX_SOURCE)
            current_sha = hashlib.sha256(raw).hexdigest()
        except (api.StoryError, OSError):
            if not historical:
                raise
            raw, current_sha = None, None
        if current_sha != sha:
            if not historical:
                api.fail('task_evidence_stale', '材料已变化，请重新读取并核对引文。', path=path)
            raw = _read(book.root, DIRECTORY + '/objects/' + sha + '.txt', MAX_SOURCE)
            if hashlib.sha256(raw).hexdigest() != sha:
                api.fail('task_record_invalid', '历史原文快照已变化，请保留材料后核对。')
        text = raw.decode('utf-8-sig')
        ref = {'path': path, 'sha256': sha}
        document_id = 'file:' + path if current_sha == sha else None
        export_changed = False
    if len(text.encode('utf-8')) > MAX_SOURCE:
        api.fail('task_source_unsafe', '证据超过读取上限。')
    start = 0
    line = source.get('line')
    quote = source.get('quote')
    if quoted:
        api.text_field(quote, 'evidence.quote', 2000)
        if line is not None:
            api.integer(line, 'evidence.line', 1)
            lines = text.splitlines(keepends=True)
            if line > len(lines):
                api.fail('invalid_evidence', '证据行号超出原文。')
            offset = sum(len(v) for v in lines[:line - 1])
            found = text.find(quote, offset)
            if found < offset or found >= offset + len(lines[line - 1]):
                api.fail('invalid_evidence', '引文不在指定起始行。')
            start = found
        else:
            if text.count(quote) != 1:
                api.fail('invalid_evidence', '引文不存在或出现多次，请补充准确起始行。')
            start = text.index(quote)
            line = text.count('\n', 0, start) + 1
        ref.update(quote=quote, line=line)
    return ref, {'text': text, 'document_id': document_id, 'path': path,
                 'source_sha256': sha, 'current_sha256': current_sha,
                 'stale': current_sha != sha or export_changed, 'line': line,
                 'start': start, 'end': start + len(quote or '')}


def _writing_scope(record):
    binding = record['binding']
    if binding['kind'] != 'writing':
        return None
    if 'complete_chapter' in binding:
        return 'complete_chapter' if binding['complete_chapter'] else 'fragment'
    if binding.get('chapter') is None:
        return 'fragment'
    # Generic legacy handoffs retain their original chapter-heading contract.
    # A known workbench envelope cannot become a fragment when its brief is bad.
    prompt = record.get('prompt', '')
    prefix = f'请继续创作本书第{binding["chapter"]}章，先核对作品、revision 和已采用细纲仍与以下写前简报一致。\n'
    if not prompt.startswith(prefix):
        return 'legacy'
    _, found, suffix = prompt.partition('本章写前简报：\n')
    if not found:
        return 'unknown'
    try:
        brief, _ = json.JSONDecoder().raw_decode(suffix.lstrip())
        scope = brief.get('delivery_scope', {})
        if (type(brief.get('schema_version')) is int and brief['schema_version'] == 1
                and brief.get('read_only') is True
                and brief.get('book_id') == binding['book_id']
                and type(brief.get('revision')) is int and brief['revision'] == binding['revision']
                and type(brief.get('chapter')) is int and brief['chapter'] == binding['chapter']
                and scope.get('mode') == 'next_chapter_draft'
                and type(scope.get('chapter')) is int and scope['chapter'] == binding['chapter']):
            return 'complete_chapter'
    except (ValueError, TypeError, AttributeError, RecursionError):
        pass
    return 'unknown'


def _complete_writing_task(record):
    return _writing_scope(record) == 'complete_chapter'


def _fragment_first_visible_line(text):
    # Apply the v2 visibility rule only to heading recognition, never to the
    # saved candidate: an invisible prefix must not hide an explicit chapter.
    for offset, char in enumerate(text):
        category = api.unicodedata.category(char)
        if (not char.isspace() and category not in ('Cc', 'Cf')
                and not category.startswith('M') and char not in api.V2_EMPTY_GLYPHS):
            return text[offset:].splitlines()[0]
    return ''


def _check_output_scope(record, text, meta=None):
    """Enforce chapter association without expanding an explicit fragment."""
    scope = _writing_scope(record)
    if scope == 'unknown':
        api.fail('task_scope_unknown', '旧完整章任务范围无法核对，请保留材料并重新生成正确范围的任务。')
    if scope == 'fragment' and not api.manuscript_counts('\n' + text, True)['visible_nonspace_v2']:
        api.fail('task_output_incomplete', '返回片段没有可读正文，请核对实际候选。')
    chapter = record['binding'].get('chapter')
    if chapter is None:
        return
    meta = meta or {}
    heading = api.first_chapter_heading(text) or ''
    match = api.COMPLETE_CHAPTER_HEADING.fullmatch(heading)
    if scope == 'fragment':
        first_line = _fragment_first_visible_line(text)
        explicit = re.match(r'^(?:#\s+)?第([0-9０-９零〇一二三四五六七八九十百千万两]+)章(?:$|[ \t　:：、.．-])',
                            first_line)
        wrong_heading = bool(explicit and api.chapter_number(explicit[1]) != chapter)
    else:
        wrong_heading = not match or api.chapter_number(match[1]) != chapter
    if (wrong_heading or meta.get('content_kind') == 'material'
            or meta.get('chapter') not in (None, chapter)):
        api.fail('task_output_scope', '返回候选的正文章号或材料类型与任务不一致。')


def _writing_length_issue(book, kind, chapter, *, complete_chapter=False):
    """Check current new-chapter policy without rewriting a saved task or plan."""
    if kind != 'writing' or chapter is None or not complete_chapter:
        return None
    if (book.db.execute('SELECT 1 FROM chapter_state WHERE chapter=?', (chapter,)).fetchone()
            or (book.meta('kind') == 'short' and 0 < chapter < book.meta('imported_through'))):
        return None
    row = book.db.execute('SELECT data FROM plans WHERE chapter=?', (chapter,)).fetchone()
    if row is None:
        return {'code': 'plan_missing', 'message': '完整章任务须先保存本章计划，再核对篇幅与细纲。'}
    try:
        plan = json.loads(row[0])
        # Stored plans without a method predate v2; do not silently reclassify.
        plan.setdefault('count_method', 'visible_nonspace_v1')
        api.valid_plan(plan)
        # This validator only needs the pinned facade's root; it reads the same
        # current author agreement as the manuscript service's commit gate.
        return api.Book._length_exception_issue(book, plan)
    except (api.StoryError, ValueError, TypeError, AttributeError, KeyError, RecursionError):
        return {'code': 'writing_plan_invalid', 'message': '当前写作章计划无法核对，请先读取并修正计划。'}


def _stale(book, record):
    reasons = []
    scope = _writing_scope(record)
    if scope == 'unknown':
        reasons.append({'code': 'task_scope_unknown', 'message': '旧完整章任务范围无法核对，请重新生成正确范围的任务。'})
    else:
        issue = _writing_length_issue(book, record['binding']['kind'], record['binding'].get('chapter'),
                                      complete_chapter=scope == 'complete_chapter')
        if issue:
            reasons.append(issue)
    if record['binding']['revision'] != book.meta('revision'):
        reasons.append({'code': 'revision_changed', 'message': '正式状态已变化，请重新核对任务范围。'})
    for source in record['binding']['sources']:
        try:
            _, resolved = _reference(book, source, historical=True)
            if resolved['stale']:
                reasons.append({'code': 'source_changed', 'source': source})
        except (api.StoryError, OSError, ValueError, UnicodeError) as error:
            reasons.append({'code': 'source_unavailable', 'source': source, 'message': str(error)})
    if record.get('result'):
        refs = ([record['result']['output']] if record['result'].get('output') else [])
        refs += [e for issue in record['result']['issues'] for e in issue['evidence']]
        for source in refs:
            try:
                _, resolved = _reference(book, source, quoted='quote' in source, historical=True)
                if resolved['stale']:
                    reasons.append({'code': 'result_evidence_changed', 'source': source})
            except (api.StoryError, OSError, ValueError, UnicodeError) as error:
                reasons.append({'code': 'result_evidence_unavailable', 'source': source, 'message': str(error)})
    return reasons


def _template(record):
    return {'schema': 1, 'task_id': record['id'], 'book_id': record['book_id'],
            'binding_sha256': record['binding_sha256'], 'summary': '填写实际完成内容及未解决事项',
            'issues': []}


def _view(book, record, detail=True):
    stale = (_stale(book, record) if detail else
             ([{'code': 'revision_changed', 'message': '正式状态已变化；打开任务核对具体来源。'}]
              if record['binding']['revision'] != book.meta('revision') else []))
    status = record['state']
    output = (record.get('result') or {}).get('output')
    chapter = record['binding'].get('chapter')
    applied = None
    if detail and status == 'reviewed' and output and chapter:
        row = book.db.execute('SELECT sha FROM chapter_state WHERE chapter=?', (chapter,)).fetchone()
        if (row and row[0] == output['sha256'] and row[0] != record['binding']['initial_formal_sha256']
                and not any(reason['code'].startswith('result_evidence') for reason in stale)):
            try:
                _, formal = _reference(book, {'chapter': chapter, 'sha256': row[0]})
            except (api.StoryError, OSError, ValueError):
                formal = {'stale': True}
            if not formal['stale']:
                status = 'applied'
                applied = {'chapter': chapter, 'sha256': row[0], 'revision': book.meta('revision'),
                           'source': 'current_formal_state'}
    result = {'id': record['id'], 'version': record['version'], 'kind': record['binding']['kind'],
              'book_id': record['book_id'], 'revision': record['binding']['revision'],
              'chapter': chapter, 'binding_sha256': record['binding_sha256'], 'status': status,
              'status_label': LABELS[status], 'created': record['created'], 'updated': record['updated'],
              'stale_reasons': stale, 'sources_checked': detail,
              'can_review': detail and not stale and status == 'needs_review',
              'execution': record['execution'], 'applied': applied,
              'host_execution': api.task_worker.view(book, record) if detail and hasattr(api, 'task_worker') else None}
    if detail:
        result.update(prompt=record['prompt'], input_sources=record['binding']['sources'],
                      result=record.get('result'), output=output,
                      issues=(record.get('result') or {}).get('issues', []),
                      issue_statuses=record.get('issue_statuses', []),
                      result_template=_template(record), failure=record.get('failure'))
    return result


def _handoff_command(arguments, windows):
    if windows:
        # PowerShell expands neither variables nor backticks inside single quotes.
        return 'Windows PowerShell', '& ' + ' '.join("'" + str(value).replace("'", "''") + "'"
                                                    for value in arguments)
    return 'POSIX shell', ' '.join(shlex.quote(str(value)) for value in arguments)


def create(root, kind, prompt, sources, chapter=None, expected_book_id=None, expected_revision=None,
           *, complete_chapter=False):
    if kind not in ('review', 'writing') or not isinstance(sources, list) or len(sources) > 32:
        api.fail('invalid_input', '助手任务类型或来源数量无效。')
    if (type(complete_chapter) is not bool or
            (complete_chapter and (kind != 'writing' or chapter is None))):
        api.fail('invalid_input', '完整章标记只适用于有明确章号的写作任务。')
    api.text_field(prompt, 'task.prompt', 150000)
    with _book(root, expected_book_id, write=True) as book:
        if expected_revision is not None and expected_revision != book.meta('revision'):
            api.fail('stale_snapshot', '任务生成期间正式状态变化，请重新生成。')
        if chapter is not None:
            api.workbench._editor_chapter(chapter)
        issue = _writing_length_issue(book, kind, chapter, complete_chapter=complete_chapter)
        if issue:
            api.fail(issue['code'], issue['message'], chapter=chapter)
        refs = []
        for source in sources:
            ref, resolved = _reference(book, source)
            if resolved['stale']:
                api.fail('stale_snapshot', '任务来源已变化，请重新生成。')
            refs.append(ref)
        if kind == 'writing' and chapter and chapter > 1:
            previous = book.db.execute('SELECT sha FROM chapter_state WHERE chapter=?', (chapter - 1,)).fetchone()
            if previous:
                ref, resolved = _reference(book, {'chapter': chapter - 1, 'sha256': previous[0]})
                if resolved['stale']:
                    api.fail('stale_snapshot', '相邻正式正文尚未完整导出或有外部改动，请先核对。')
                refs.append(ref)
        row = book.db.execute('SELECT sha FROM chapter_state WHERE chapter=?', (chapter,)).fetchone()
        binding = {'kind': kind, 'book_id': book.meta('id'), 'revision': book.meta('revision'),
                   'chapter': chapter, 'sources': refs, 'initial_formal_sha256': row[0] if row else None}
        if kind == 'writing':
            binding['complete_chapter'] = complete_chapter
        task_id = uuid.uuid4().hex
        record = {'schema': 1, 'id': task_id, 'version': 1, 'book_id': book.meta('id'),
                  'binding': binding, 'binding_sha256': api.digest(api.dumps(binding)),
                  'state': 'generated', 'created': _now(), 'updated': _now(),
                  'execution': {'source': 'none', 'status': 'unverified'}, 'result': None}
        shell, command = _handoff_command((
            sys.executable, api.__file__, 'task-return', '--book', book.root, '--task-id', task_id,
            '--version', 1, '--result-file', '.story/drafts/assistant-result-' + task_id + '.json'),
            windows=os.name == 'nt')
        record['prompt'] = (prompt + '\n\n助手任务回流：本任务不授权额外修改或正式采用。完成既定工作后，将下面 JSON 模板填为实际结果，'
            '保存到命令所示的书内结果文件，再执行回填命令。正文另存本书 .story/drafts/ 下候选，不放进回执。'
            '有候选时添加 output: {path: "书内候选相对路径", sha256: "实际文件SHA-256"}；'
            '审稿问题用 issues: [{id, severity: "blocker或advice", issue, evidence: [{chapter或path, sha256, quote, line}]}]，'
            '引文可连到前文正式章和当前候选；行号从1开始。没有完成的内容如实记录，不宣称真人反馈或平台状态。\n'
            + json.dumps(_template(record), ensure_ascii=False, indent=2) + '\n回填命令（' + shell + '）：\n' + command +
            '\n若任务版本变化，先 task-get --book <同一书根> --task-id ' + task_id + ' 核对当前版本；不要改写绑定值。')
        for ref in refs:
            _source_snapshot(book, ref)
        _save(book, record)
        return {'ok': True, 'task': _view(book, record)}


def get(root, task_id, expected_book_id=None):
    with _book(root, expected_book_id, write=True) as book:
        record, _ = _record(book, task_id)
        return {'ok': True, 'task': _view(book, record)}


def list_tasks(root, offset=0, limit=20, expected_book_id=None):
    api.integer(offset, 'offset')
    api.integer(limit, 'limit', 1)
    if limit > 50:
        api.fail('invalid_input', '每页最多50个任务。')
    with _book(root, expected_book_id, write=True) as book:
        path = api.safe_path(book.root, DIRECTORY)
        names, total = [], 0
        keep = offset + limit
        directory = None
        try:
            with api._pinned_read_directory(path) as directory:
                with os.scandir(directory.fd if directory.fd is not None else directory.path) as entries:
                    for entry in entries:
                        if re.fullmatch(r'[0-9a-f]{32}\.json', entry.name):
                            total += 1
                            item = (entry.stat(follow_symlinks=False).st_mtime_ns, entry.name)
                            # Keep only enough metadata for this page while counting
                            # every task; manuscripts and other records remain unread.
                            if len(names) < keep:
                                heapq.heappush(names, item)
                            elif item > names[0]:
                                heapq.heapreplace(names, item)
                api._verify_bound_directory(directory)
        except FileNotFoundError:
            if directory is not None:
                api.fail('task_catalog_changed', '任务目录在读取期间变化，请刷新后重新读取。')
        # Most recently written records first; ID breaks equal-time ties.
        names.sort(reverse=True)
        rows = []
        for _, name in names[offset:offset + limit]:
            record, _ = _record(book, name[:-5])
            rows.append(_view(book, record, detail=False))
        return {'ok': True, 'book_id': book.meta('id'), 'tasks': rows, 'offset': offset,
                'limit': limit, 'total': total, 'has_more': offset + limit < total}


def _change(root, task_id, version, callback, expected_book_id=None):
    api.integer(version, 'task.version', 1)
    with _book(root, expected_book_id, write=True) as book:
        record, fingerprint = _record(book, task_id)
        if record['version'] != version:
            api.fail('task_version_changed', '任务记录已经更新，请刷新后重试。', version=record['version'])
        callback(book, record)
        record['version'] += 1
        record['updated'] = _now()
        _save(book, record, fingerprint)
        return {'ok': True, 'task': _view(book, record)}


def mark_handoff(root, task_id, version, expected_book_id=None):
    def change(book, record):
        if record['state'] not in ('generated', 'handed_off'):
            api.fail('task_state_conflict', '已有结果或失败记录的任务不能改回交接状态。')
        if _stale(book, record):
            api.fail('stale_snapshot', '任务来源已变化，请重新生成。')
        record['state'] = 'handed_off'
        record['execution'] = {'source': 'author_handoff', 'status': 'unverified'}
    return _change(root, task_id, version, change, expected_book_id)


def mark_failed(root, task_id, version, reason, expected_book_id=None):
    api.text_field(reason, 'failure', 2000)
    def change(book, record):
        if record['state'] in ('needs_review', 'reviewed'):
            api.fail('task_state_conflict', '已回流结果不能由失败说明覆盖。')
        record['state'] = 'failed'
        record['failure'] = reason
        record['execution'] = {'source': 'author_report', 'status': 'unverified'}
    return _change(root, task_id, version, change, expected_book_id)


def _result(book, record, value):
    if (not isinstance(value, dict) or set(value) - {'schema', 'task_id', 'book_id', 'binding_sha256', 'summary', 'output', 'issues'}
            or type(value.get('schema')) is not int or value['schema'] != 1 or
            value.get('task_id') != record['id'] or value.get('book_id') != record['book_id'] or
            value.get('binding_sha256') != record['binding_sha256']):
        api.fail('task_result_mismatch', '回流结果的任务、作品或输入绑定不匹配。')
    summary = api.text_field(value.get('summary'), 'result.summary', 4000)
    if summary == '填写实际完成内容及未解决事项':
        api.fail('placeholder', '请填写实际结果，不能回填空模板。')
    output = value.get('output')
    if record['binding']['kind'] == 'writing' and output is None:
        api.fail('task_output_missing', '写作任务须返回已保存候选；未写成请记录失败原因。')
    if output is not None:
        if not isinstance(output, dict) or set(output) != {'path', 'sha256'}:
            api.fail('invalid_input', '候选输出只接受路径和校验值。')
        if not _relative(output['path']).startswith('.story/drafts/') or Path(output['path']).suffix.lower() not in ('.md', '.txt'):
            api.fail('invalid_input', '回流正文必须已保存为本书 .story/drafts/ 下的 Markdown 或文本候选。')
        output, resolved = _reference(book, output)
        meta = api.workbench._candidate_metadata(book.root, output['path'])
        if meta.get('recovery') or '/自动恢复/' in output['path']:
            api.fail('task_output_incomplete', '候选保存尚未完整，请先恢复并核对。')
        _check_output_scope(record, resolved['text'], meta)
    issues = value.get('issues')
    if not isinstance(issues, list) or len(issues) > 60:
        api.fail('invalid_input', '问题列表最多60项。')
    validated, ids = [], set()
    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != {'id', 'severity', 'issue', 'evidence'}:
            api.fail('invalid_input', '每个审稿问题须有标识、严重度、说明和证据。')
        ident = issue['id']
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ident) or ident in ids:
            api.fail('invalid_input', '审稿问题标识无效或重复。')
        ids.add(ident)
        if issue['severity'] not in ('blocker', 'advice'):
            api.fail('invalid_input', '问题严重度须为 blocker 或 advice。')
        api.text_field(issue['issue'], 'issue', 2000)
        refs = issue['evidence']
        if not isinstance(refs, list) or not 1 <= len(refs) <= 6:
            api.fail('invalid_input', '每个问题须提供1至6处原文依据。')
        validated.append({**issue, 'evidence': [_reference(book, ref, quoted=True)[0] for ref in refs]})
    return {**_template(record), 'summary': summary, 'issues': validated, **({'output': output} if output else {})}


def import_result(root, task_id, version, result, expected_book_id=None, source='manual_import'):
    if source not in ('manual_import', 'local_cli'):
        api.fail('invalid_input', '结果来源无效。')
    api.integer(version, 'task.version', 1)
    with _book(root, expected_book_id, write=True) as book:
        record, fingerprint = _record(book, task_id)
        validated = _result(book, record, result)
        if record.get('result') == validated:
            return {'ok': True, 'unchanged': True, 'task': _view(book, record)}
        api.integer(version, 'task.version', 1)
        if record.get('result') is not None:
            api.fail('task_result_conflict', '已有回流结果不能覆盖；请保留原结果并创建新的任务。')
        # The original generated prompt survives a handoff, but a later failure
        # is an author decision that a late invocation must not silently undo.
        if (version > record['version'] or
                (version < record['version'] and record['state'] not in ('generated', 'handed_off'))):
            api.fail('task_version_changed', '任务记录已更新，请刷新后回填。', version=record['version'])
        record['result'] = validated
        record['state'] = 'needs_review'
        record['execution'] = {'source': source, 'status': 'unverified',
                               'note': '已核对回流文件与输入绑定；未认证助手实际执行轨迹或语义结论。'}
        record['issue_statuses'] = [{'id': issue['id'], 'status': 'open', 'note': ''} for issue in validated['issues']]
        record['version'] += 1
        record['updated'] = _now()
        refs = ([validated['output']] if validated.get('output') else [])
        refs += [ref for issue in validated['issues'] for ref in issue['evidence']]
        for ref in refs:
            _source_snapshot(book, ref)
        _save(book, record, fingerprint)
        return {'ok': True, 'task': _view(book, record)}


def review(root, task_id, version, issue_statuses=None, expected_book_id=None):
    def change(book, record):
        if record['state'] != 'needs_review':
            api.fail('task_state_conflict', '只能核对已回流的结果。')
        stale = _stale(book, record)
        if stale:
            api.fail('stale_snapshot', '任务来源或结果版本已经变化，旧结果不能直接确认。', stale_reasons=stale)
        statuses = {v['id']: v for v in record['issue_statuses']}
        if issue_statuses is not None:
            if not isinstance(issue_statuses, list) or len(issue_statuses) > 60:
                api.fail('invalid_input', '问题处理记录无效。')
            seen = set()
            for item in issue_statuses:
                if (not isinstance(item, dict) or set(item) != {'id', 'status', 'note'} or
                        item['id'] not in statuses or item['id'] in seen or
                        item['status'] not in ('open', 'resolved', 'dismissed')):
                    api.fail('invalid_input', '问题处理记录须对应本任务的问题且不可重复。')
                api.text_field(item['note'], 'issue.note', 1200)
                seen.add(item['id'])
                statuses[item['id']] = dict(item)
        record['issue_statuses'] = list(statuses.values())
        if not any(issue['severity'] == 'blocker' and statuses[issue['id']]['status'] == 'open'
                   for issue in record['result']['issues']):
            record['state'] = 'reviewed'
            record['reviewed_at'] = _now()
    return _change(root, task_id, version, change, expected_book_id)


def evidence(root, task_id, issue_id, index, expected_book_id=None):
    api.integer(index, 'evidence.index')
    with _book(root, expected_book_id, write=True) as book:
        record, _ = _record(book, task_id)
        issue = next((v for v in (record.get('result') or {}).get('issues', []) if v['id'] == issue_id), None)
        if issue is None or index >= len(issue['evidence']):
            api.fail('invalid_input', '审稿问题或原文位置不存在。')
        ref, resolved = _reference(book, issue['evidence'][index], quoted=True, historical=True)
        return {'ok': True, 'task_id': task_id, 'issue_id': issue_id, 'evidence': ref, **resolved}


def read_result_file(root, relative):
    relative = _relative(relative)
    if not relative.startswith('.story/drafts/') or not relative.endswith('.json'):
        api.fail('invalid_input', '助手结果文件应保存于本书 .story/drafts/ 下，扩展名为 .json。')
    with _book(root) as book:
        try:
            return json.loads(_read(book.root, relative))
        except (ValueError, UnicodeError, RecursionError) as error:
            api.fail('invalid_input', '助手结果文件不是有效 JSON。', reason=str(error))


# Explicit managed execution is separate from manual/unverified handoffs.
def execution_start(root, task_id, version, request_id, expected_book_id=None, timeout_seconds=900):
    return api.task_worker.start(root, task_id, version, request_id, expected_book_id, timeout_seconds)


def execution_get(root, task_id, expected_book_id=None):
    return api.task_worker.get(root, task_id, expected_book_id)


def execution_cancel(root, task_id, execution_id, expected_book_id=None):
    return api.task_worker.cancel(root, task_id, execution_id, expected_book_id)


def execution_reconcile(root, task_id, execution_id, expected_book_id=None):
    return api.task_worker.reconcile(root, task_id, execution_id, expected_book_id)


def execution_busy(root, expected_book_id=None):
    return api.task_worker.busy(root, expected_book_id)
