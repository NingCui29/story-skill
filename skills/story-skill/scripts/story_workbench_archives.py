"""Verified local book archives; restore always creates an independent directory.

The SQLite online backup is the database payload. Local files are read without
following links and checked again before publishing. Browser buffers and linked
external materials are deliberately outside this on-disk backup contract.
"""
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import tempfile
import unicodedata
import zipfile


CONTRACT = 'story.book-archive.v1'
MANIFEST_NAME = 'story-book-archive.json'
DATABASE = '.story/state.sqlite3'
DATABASES = (DATABASE, '.story/publishing.sqlite3')
MAX_FILES = 20000
MAX_FILE_BYTES = 2 * 1024 ** 3
MAX_TOTAL_BYTES = 8 * 1024 ** 3
MAX_MANIFEST_BYTES = 16 * 1024 ** 2


def inject(core):
    global api
    api = core


def _fail(code, message, **details):
    api.fail('archive_' + code, message, **details)


def _absolute(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        _fail('path', '请填写明确的本地文件或目录。')
    return Path(os.path.abspath(Path(value).expanduser()))


def _safe_relative(value):
    # Portable across macOS and Windows, including ADS and Win32 device names.
    if (not isinstance(value, str) or not value or len(value) > 4096 or
            re.search(r'[<>:"\\|?*\x00-\x1f]', value)):
        _fail('unsafe_path', '备份包含不安全的文件路径。')
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(
            part in ('', '.', '..') or part.rstrip(' .') != part or
            re.match(r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', part, re.I)
            for part in path.parts):
        _fail('unsafe_path', '备份包含不安全或无法跨平台恢复的文件路径。', path=value)
    return value


def _path_key(value):
    return unicodedata.normalize('NFC', value).casefold()


def _excluded(relative):
    parts = PurePosixPath(relative).parts
    if any(re.fullmatch(r'\.story-pin-[0-9a-f]{32}', part) for part in parts):
        return True
    if any(part in ('.git', '__pycache__', '.DS_Store') for part in parts):
        return True
    if relative in ('service.json', 'server.lock', '.story/workbench-server.lock'):
        return True
    if parts[-1] in ('service.json', 'workbench-service.json', 'library-service.json',
                     'workbench-server.lock', 'server.lock') and '.backups' in parts:
        return True
    if any(relative in (database + '-wal', database + '-shm', database + '-journal') for database in DATABASES):
        return True
    if relative.startswith(('.story/workbench-service', '.story/.workbench-backups/',
                            '.story/workbench/')):
        return True
    return False


def _scan(root):
    """Inventory regular local files; never descend into links or junctions."""
    files, excluded, pending, seen, total = {}, [], [('', root)], 0, 0
    while pending:
        prefix, directory_path = pending.pop()
        with api._pinned_directory(directory_path) as directory:
            with os.scandir(directory.fd if directory.fd is not None else directory.path) as entries:
                rows = sorted(entries, key=lambda item: item.name)
            for item in rows:
                # Windows directory pins create DELETE_ON_CLOSE guards. Their
                # names change on every scan; neither payload nor exclusion
                # evidence may depend on these private transient entries.
                if re.fullmatch(r'\.story-pin-[0-9a-f]{32}', item.name):
                    continue
                seen += 1
                if seen > MAX_FILES * 2:
                    _fail('limit', '作品目录项目过多，请先整理后备份。', maximum=MAX_FILES * 2)
                relative = prefix + item.name
                if _excluded(relative):
                    excluded.append({'path': relative, 'reason': 'transient_or_service'})
                    continue
                # Windows DirEntry.stat() reports zero st_dev/st_ino/st_nlink.
                # Read fresh metadata through the pinned parent so identity
                # and hard-link checks use real values on both platforms.
                # https://docs.python.org/3.12/library/os.html#os.DirEntry.stat
                value = (os.stat(item.name, dir_fd=directory.fd, follow_symlinks=False)
                         if directory.fd is not None else
                         os.stat(directory.path / item.name, follow_symlinks=False))
                if (stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400 or
                        getattr(root / relative, 'is_junction', lambda: False)()):
                    excluded.append({'path': relative, 'reason': 'linked_material'})
                    continue
                _safe_relative(relative)
                if relative != DATABASE and relative.endswith('/' + DATABASE):
                    _fail('nested_book', '作品目录内包含另一部已初始化作品，请分别备份。', path=relative)
                if stat.S_ISDIR(value.st_mode):
                    pending.append((relative + '/', root / relative))
                elif stat.S_ISREG(value.st_mode):
                    if value.st_nlink != 1:
                        _fail('linked_file', '作品包含硬链接文件，请先核对并改为本地独立副本。', path=relative)
                    if value.st_size > MAX_FILE_BYTES:
                        _fail('limit', '单个作品文件超过备份上限。', path=relative, maximum=MAX_FILE_BYTES)
                    total += value.st_size
                    if total > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
                        _fail('limit', '作品文件总量超过备份上限。', maximum_bytes=MAX_TOTAL_BYTES, maximum_files=MAX_FILES)
                    files[relative] = (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
                else:
                    _fail('special_file', '作品包含无法安全备份的特殊文件。', path=relative)
            api._verify_bound_directory(directory)
    if DATABASE not in files:
        _fail('missing_state', '作品缺少状态库，无法制作可恢复的备份。')
    return files, excluded


def _hash_stream(stream, output=None, maximum=MAX_FILE_BYTES):
    digest, size = hashlib.sha256(), 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        size += len(chunk)
        if size > maximum:
            _fail('limit', '文件在读取期间超过备份上限。')
        digest.update(chunk)
        if output is not None:
            output.write(chunk)
    return digest.hexdigest(), size


def _read_file(root, relative, writer=None):
    path = api.safe_path(root, relative)
    with api._pinned_directory(path.parent) as directory:
        with api._bound_reader(api._BoundFile(directory, path.name)) as source:
            before = os.fstat(source.fileno())
            if before.st_nlink != 1:
                _fail('linked_file', '文件在备份期间变为硬链接。', path=relative)
            result = _hash_stream(source, writer)
            after = os.fstat(source.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                _fail('changed', '文件在备份期间发生变化，请保存后重试。', path=relative)
        api._verify_bound_directory(directory)
    return result


def _identity(book, expected=None):
    book_id, revision = book.meta('id'), book.meta('revision')
    title, kind = book.meta('title'), book.meta('kind')
    if (not isinstance(book_id, str) or not book_id or type(revision) is not int or revision < 0 or
            not isinstance(title, str) or kind not in ('long', 'short', 'analysis')):
        _fail('state', '作品身份或版本信息无效。')
    if expected is not None and book_id != expected:
        _fail('identity', '作品身份已改变，已停止备份。')
    return {'book_id': book_id, 'revision': revision, 'title': title, 'kind': kind}


def create_archive(root, output_path, expected_book_id=None):
    """Publish one verified ZIP without replacing any existing output file."""
    root, output_path = _absolute(root), _absolute(output_path)
    if output_path.suffix.lower() != '.zip':
        _fail('path', '备份文件应使用 .zip 扩展名。')
    try:
        output_path.relative_to(root)
    except ValueError:
        pass
    else:
        _fail('path', '请选择作品目录之外的备份位置。')
    with ExitStack() as stack:
        stack.enter_context(api.workbench_books.lifecycle_guard(api, root))
        root_pin = stack.enter_context(api._pinned_directory(root))
        output_pin = stack.enter_context(api._pinned_directory(output_path.parent))
        target = api._BoundFile(output_pin, output_path.name)
        if api._bound_exists(target):
            _fail('exists', '备份文件已存在，请使用新文件名。', path=str(output_path))
        book = api.workbench._ReadOnlyBook(root)
        stack.callback(book.close)
        identity = _identity(book, expected_book_id)
        data_version = book.db.execute('PRAGMA data_version').fetchone()[0]
        inventory, excluded = _scan(root)
        work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='.story-archive-', dir=str(output_path.parent))))
        databases = {DATABASE: book.db}
        for relative in DATABASES[1:]:
            if relative in inventory:
                path = root / relative
                directory = stack.enter_context(api._pinned_directory(path.parent))
                databases[relative] = stack.enter_context(api.publish._connection(api._BoundFile(directory, path.name), False))
                api.publish._validate_database(databases[relative], identity['book_id'])
        versions = {path: db.execute('PRAGMA data_version').fetchone()[0] for path, db in databases.items()}
        snapshots = {}
        for index, (relative, db) in enumerate(databases.items()):
            snapshot = work / ('database-' + str(index) + '.sqlite3')
            # SQLite's connection context commits/rolls back but leaves the
            # handle open. Close it before the temporary directory is removed,
            # including on Windows where open database files cannot be unlinked.
            with closing(sqlite3.connect(snapshot)) as destination:
                db.backup(destination)
                destination.execute('PRAGMA journal_mode=DELETE')
                if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    _fail('state', '状态库快照未通过完整性检查。', path=relative)
            snapshots[relative] = snapshot
        staged = work / 'book.zip'
        manifest = {'contract': CONTRACT, 'created_at': datetime.now(timezone.utc).isoformat(),
                    **identity, 'schema': api.SCHEMA_VERSION, 'files': [], 'excluded': excluded,
                    'scope': 'saved_local_book_files', 'external_materials_included': False,
                    'unsaved_browser_buffers_included': False}
        with zipfile.ZipFile(staged, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for relative in sorted(inventory):
                with archive.open('book/' + relative, 'w', force_zip64=True) as writer:
                    if relative in snapshots:
                        with snapshots[relative].open('rb') as source:
                            digest, size = _hash_stream(source, writer)
                    else:
                        digest, size = _read_file(root, relative, writer)
                manifest['files'].append({'path': relative, 'sha256': digest, 'bytes': size})
            final_inventory, final_excluded = _scan(root)
            api._verify_bound_directory(root_pin)
            # The database payload is a SQLite snapshot. Its raw file timestamps
            # may change during checkpointing without changing logical contents.
            if (set(inventory) != set(final_inventory) or
                    any(inventory[path][:2] != final_inventory[path][:2] for path in snapshots if path in final_inventory) or
                    {k: v for k, v in inventory.items() if k not in snapshots} !=
                    {k: v for k, v in final_inventory.items() if k not in snapshots} or
                    excluded != final_excluded):
                _fail('changed', '作品文件集合在备份期间发生变化，请保存后重试。')
            for row in manifest['files']:
                if row['path'] not in snapshots and _read_file(root, row['path']) != (row['sha256'], row['bytes']):
                    _fail('changed', '作品文件在备份期间发生变化，请保存后重试。', path=row['path'])
            if (_identity(book, expected_book_id) != identity or
                    book.db.execute('PRAGMA data_version').fetchone()[0] != data_version or
                    any(db.execute('PRAGMA data_version').fetchone()[0] != versions[path] for path, db in databases.items())):
                _fail('changed', '作品状态在备份期间发生变化，请保存后重试。')
            book.verify_state_path()
            api._verify_bound_directory(root_pin)
            payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode('utf-8')
            if len(payload) > MAX_MANIFEST_BYTES:
                _fail('limit', '备份清单超过上限。')
            archive.writestr(MANIFEST_NAME, payload)
        # FlushFileBuffers/_commit on Windows requires a writable handle. This
        # is our unpublished stage, opened without truncating its verified ZIP.
        with staged.open('r+b') as completed:
            os.fsync(completed.fileno())
        # Inspect the exact stage before publication; no success on an invalid
        # archive and no replacing a competing backup with the same filename.
        with api._pinned_directory(work) as staging_pin:
            source = api._BoundFile(staging_pin, staged.name)
            with api._bound_reader(source) as completed:
                expected_archive_sha, expected_archive_size = _hash_stream(
                    completed, maximum=MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES + MAX_FILES * 16384)
            # Bind inspection to the bytes that will be published, rather than
            # trusting a previously checked pathname after a competing rename.
            inspected = inspect_archive(staged)
            if inspected['manifest_sha256'] != hashlib.sha256(payload).hexdigest():
                _fail('changed', '备份暂存文件在发布前被替换，已停止发布。')
            api._verify_bound_directory(output_pin)
            try:
                api._publish_no_replace(source, target)
            except FileExistsError:
                _fail('exists', '备份文件名已被另一操作使用，请使用新文件名。')
            if api._bound_exists(source):
                api._bound_unlink(source)
        with api._bound_reader(target) as completed:
            # A whole archive may exceed the per-book-file ceiling. Account
            # for bounded manifest and ZIP header/path overhead separately.
            digest, size = _hash_stream(completed, maximum=MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES + MAX_FILES * 16384)
        if (digest, size) != (expected_archive_sha, expected_archive_size):
            _fail('changed', '备份发布后的内容与已核验文件不一致，请保留文件核对。', path=str(output_path))
        api.publish._bound_stat(target)
        api._verify_bound_directory(output_pin)
        return {**inspected, 'ok': True, 'path': str(output_path), 'sha256': digest,
                'archive_bytes': size, 'note': '备份包含已落盘的书稿和状态；未保存页面文字及关联目录须另行保存。'}


def _validated_manifest(archive):
    rows = archive.infolist()
    if len(rows) > MAX_FILES + 1:
        _fail('limit', '备份条目超过上限。')
    by_name, folded, total = {}, set(), 0
    for row in rows:
        _safe_relative(row.filename)
        if (row.filename in by_name or _path_key(row.filename) in folded or row.is_dir() or
                row.flag_bits & 1 or stat.S_ISLNK(row.external_attr >> 16) or
                (stat.S_IFMT(row.external_attr >> 16) not in (0, stat.S_IFREG))):
            _fail('unsafe_archive', '备份包含重复、加密、链接或特殊条目。')
        maximum = MAX_MANIFEST_BYTES if row.filename == MANIFEST_NAME else MAX_FILE_BYTES
        if row.file_size > maximum or row.file_size < 0:
            _fail('limit', '备份条目超过大小上限。')
        total += row.file_size
        if total > MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES:
            _fail('limit', '备份解压总量超过上限。')
        by_name[row.filename] = row
        folded.add(_path_key(row.filename))
    if MANIFEST_NAME not in by_name:
        _fail('manifest', '这不是工作台整书备份。')
    try:
        manifest = json.loads(archive.read(MANIFEST_NAME).decode('utf-8'))
    except (ValueError, UnicodeError, RuntimeError) as error:
        _fail('manifest', '备份清单无法读取。', reason=str(error))
    if (not isinstance(manifest, dict) or manifest.get('contract') != CONTRACT or
            manifest.get('schema') != api.SCHEMA_VERSION or
            not isinstance(manifest.get('book_id'), str) or not manifest['book_id'] or
            type(manifest.get('revision')) is not int or manifest['revision'] < 0 or
            not isinstance(manifest.get('title'), str) or manifest.get('kind') not in ('long', 'short', 'analysis') or
            not isinstance(manifest.get('files'), list) or len(manifest['files']) > MAX_FILES):
        _fail('manifest', '备份清单身份、版本或格式无效。')
    expected, paths, path_keys, payload_total, directory_names = {MANIFEST_NAME}, set(), set(), 0, {}
    for row in manifest['files']:
        if not isinstance(row, dict):
            _fail('manifest', '备份文件清单格式无效。')
        relative = _safe_relative(row.get('path'))
        if relative != DATABASE and relative.endswith('/' + DATABASE):
            _fail('nested_book', '备份包含嵌套的独立作品，请分别恢复。', path=relative)
        if _path_key(relative) in path_keys:
            _fail('manifest', '备份文件清单包含重复路径。')
        if _excluded(relative) or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('sha256', ''))) or type(row.get('bytes')) is not int:
            _fail('manifest', '备份文件清单含有无效的校验信息或临时服务文件。')
        name = 'book/' + relative
        if name not in by_name or by_name[name].file_size != row['bytes']:
            _fail('manifest', '备份清单与实际文件不一致。', path=relative)
        payload_total += row['bytes']
        if payload_total > MAX_TOTAL_BYTES:
            _fail('limit', '备份文件总量超过恢复上限。')
        expected.add(name)
        paths.add(relative)
        path_keys.add(_path_key(relative))
        for parent in PurePosixPath(relative).parents:
            name = parent.as_posix()
            if name == '.':
                continue
            key = _path_key(name)
            if key in directory_names and directory_names[key] != name:
                _fail('unsafe_path', '备份目录名称在不同系统中可能冲突。')
            directory_names[key] = name
    if set(by_name) != expected or DATABASE not in paths:
        _fail('manifest', '备份缺少状态库或含有未登记文件。')
    for relative in paths:
        if any(_path_key(parent.as_posix()) in path_keys for parent in PurePosixPath(relative).parents if parent.as_posix() != '.'):
            _fail('unsafe_path', '备份路径同时被用作文件和目录。')
    return manifest


def _open_verified_archive(path, stack):
    path = _absolute(path)
    directory = stack.enter_context(api._pinned_directory(path.parent))
    file = api._BoundFile(directory, path.name)
    expected = api.publish._bound_stat(file)
    stream = stack.enter_context(api._bound_reader(file))
    opened = os.fstat(stream.fileno())

    def key(value):
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns

    def identity(value):
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

    def verify():
        try:
            current_handle, current_path = os.fstat(stream.fileno()), api.publish._bound_stat(file)
            # CPython 3.12 Windows path stat exposes creation time as ctime,
            # while fstat exposes ChangeTime. Bind their common identity, then
            # retain each API's complete metadata as its own mutation baseline.
            if (identity(expected) != identity(opened) or key(opened) != key(current_handle) or
                    key(expected) != key(current_path) or identity(current_path) != identity(current_handle)):
                _fail('changed', '备份文件在读取期间被修改或替换，请重新选择并核对。', path=str(path),
                      expected_path=key(expected), opened_handle=key(opened),
                      current_path=key(current_path), current_handle=key(current_handle))
            api._verify_bound_directory(directory)
        except OSError as error:
            _fail('changed', '备份文件在读取期间失去原路径，请重新选择并核对。', path=str(path), reason=str(error))
        except api.StoryError as error:
            if error.code == 'archive_changed':
                raise
            _fail('changed', '备份文件在读取期间的路径身份无法核对，请重新选择。', path=str(path), reason=str(error))

    verify()
    archive = stack.enter_context(zipfile.ZipFile(stream, 'r'))
    stack.callback(verify)
    manifest = _validated_manifest(archive)
    return archive, manifest, verify


def inspect_archive(path):
    """Validate every decompressed file hash before offering restoration."""
    try:
        with ExitStack() as stack:
            archive, manifest, verify = _open_verified_archive(path, stack)
            total = 0
            for row in manifest['files']:
                with archive.open('book/' + row['path']) as stream:
                    digest, size = _hash_stream(stream)
                if (digest, size) != (row['sha256'], row['bytes']):
                    _fail('checksum', '备份文件校验失败。', path=row['path'])
                total += size
            return {key: manifest[key] for key in ('contract', 'book_id', 'revision', 'title', 'kind')} | {
                'files': len(manifest['files']), 'bytes': total, 'excluded': manifest.get('excluded', []),
                'manifest_sha256': hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest(),
                'restore_note': '恢复至新目录并保留原作品身份；核对后再加入书架，避免同时编辑两个副本。'}
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError, NotImplementedError) as error:
        _fail('invalid_zip', '备份压缩包损坏或格式不支持。', reason=str(error))


def restore_archive(archive_path, destination_root):
    """Restore into a fresh sibling stage, verify, then publish without replacement."""
    destination_root = _absolute(destination_root)
    with ExitStack() as stack:
        stack.enter_context(api.workbench_books.lifecycle_guard(api, destination_root))
        parent = stack.enter_context(api._pinned_directory(destination_root.parent))
        if os.path.lexists(destination_root):
            _fail('exists', '恢复目录已存在，请选择一个全新的目录。', path=str(destination_root))
        try:
            archive, manifest, verify = _open_verified_archive(archive_path, stack)
            work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='.story-restore-', dir=str(parent.path))))
            staged_root = work / 'book'
            staged_root.mkdir()
            for row in manifest['files']:
                target = api.safe_path(staged_root, row['path'])
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open('book/' + row['path']) as source, target.open('xb') as writer:
                    digest, size = _hash_stream(source, writer)
                    writer.flush()
                    os.fsync(writer.fileno())
                if (digest, size) != (row['sha256'], row['bytes']):
                    _fail('checksum', '备份文件校验失败，恢复目录未发布。', path=row['path'])
            book = api.workbench._ReadOnlyBook(staged_root)
            try:
                identity = _identity(book, manifest['book_id'])
                if identity != {key: manifest[key] for key in identity}:
                    _fail('identity', '备份状态库与清单身份不一致。')
                if book.db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    _fail('state', '恢复的状态库未通过完整性检查。')
                book.verify_state_path()
            finally:
                book.close()
            verify()
            api._verify_bound_directory(parent)
            # Reserve the target directory using mkdir, which is no-replace on
            # both platforms, and move children into that owned destination.
            # A failed publish leaves a clearly marked partial directory rather
            # than overwriting an unrelated directory or original manuscript.
            try:
                destination_root.mkdir()
            except FileExistsError:
                _fail('exists', '恢复目录已被另一操作使用，请选择新目录。')
            try:
                with api._pinned_directory(destination_root) as destination:
                    marker = api._BoundFile(destination, '.story-restore-incomplete.json')
                    fd, marker_stage = api._bound_stage(destination, '.story-restore-marker-')
                    try:
                        with api._owned_fdopen(fd, 'wb') as writer:
                            writer.write(json.dumps({'contract': CONTRACT, 'book_id': manifest['book_id'],
                                                     'note': '恢复发布未完成，请保留此目录并核对。'}, ensure_ascii=False).encode('utf-8'))
                            writer.flush()
                            os.fsync(writer.fileno())
                        api._publish_no_replace(marker_stage, marker)
                    finally:
                        if api._bound_exists(marker_stage):
                            api._bound_unlink(marker_stage)
                    for row in manifest['files']:
                        api._verify_bound_directory(destination)
                        item = api.safe_path(staged_root, row['path'])
                        target = api.safe_path(destination_root, row['path'])
                        with api._pinned_directory(item.parent) as source_parent, api._pinned_directory(target.parent, create=True) as target_parent:
                            source = api._BoundFile(source_parent, item.name)
                            api._publish_no_replace(source, api._BoundFile(target_parent, target.name))
                            if api._bound_exists(source):
                                api._bound_unlink(source)
                            api._verify_bound_directory(target_parent)
                    for row in manifest['files']:
                        if _read_file(destination_root, row['path']) != (row['sha256'], row['bytes']):
                            _fail('checksum', '恢复发布后文件校验失败，请保留目录核对。', path=row['path'])
                    restored_book = api.workbench._ReadOnlyBook(destination_root)
                    try:
                        if _identity(restored_book, manifest['book_id']) != identity:
                            _fail('identity', '恢复发布后作品身份发生变化。')
                        restored_book.verify_state_path()
                    finally:
                        restored_book.close()
                    verify()
                    api._verify_bound_directory(destination)
                    api._verify_bound_directory(parent)
                    api._bound_unlink(marker)
            except (OSError, api.StoryError) as error:
                _fail('partial_restore', '恢复发布未完成，已保留临时标记和现有文件，请核对后重试到新目录。',
                      path=str(destination_root), reason=str(error))
            return {'ok': True, 'root': str(destination_root), **identity, 'files': len(manifest['files']),
                    'note': '已恢复到新目录，保留原作品身份。请先核对内容，避免同时编辑原作与恢复副本。'}
        except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError, NotImplementedError) as error:
            _fail('invalid_zip', '备份压缩包损坏或格式不支持。', reason=str(error))


def recovery_catalog(root, expected_book_id=None):
    """Surface existing disk recoveries using the editor's own document rules."""
    root = _absolute(root)
    book = api.workbench._ReadOnlyBook(root)
    try:
        identity = _identity(book, expected_book_id)
        warnings = []
        rows = [row for row in api.workbench._author_files(root, warnings)
                if row['category'] == '自动恢复']
        visible = sorted(rows, key=lambda row: (row['modified'], row['path']), reverse=True)[:100]
        for row in visible:
            pending = row['id'].startswith('pending:')
            row['status'] = '保存待核对' if pending else '自动恢复稿'
            try:
                maximum = api.workbench.PENDING_SAVE_LIMIT if pending else api.workbench.AUTHOR_TEXT_LIMIT
                row['sha256'] = hashlib.sha256(api.workbench._author_read(root, row['path'], maximum)).hexdigest()
            except (OSError, api.StoryError) as error:
                row['status'] = '读取待核对'
                row['read_error'] = str(error)
        book.verify_state_path()
        return {'ok': True, **identity, 'count': len(rows),
                'recoveries': visible,
                'truncated': len(rows) > 100, 'warnings': warnings,
                'note': '恢复稿与保存待核对记录须打开核对；存在恢复稿不表示有未保存页面或已经正式采用。'}
    finally:
        book.close()
