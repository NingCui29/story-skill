"""Fail-closed Windows installation handoff, run by an independent temporary Python.

Never terminate a process. Only the authenticated local service protocol can stop
an owned service, and only after the caller confirms all editor windows are saved.
The old installation is renamed intact; it is never recursively deleted here.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import uuid

UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8BB803B9-A7FA-47E8-8C3C-E5A6FF197702}_is1'
MAX_JSON = 8 * 1024 * 1024
SHARING_ERRORS = (32, 33)


class LifecycleError(RuntimeError):
    pass


class SaveConfirmationRequired(LifecycleError):
    pass


def _plain(path: Path):
    """Reject links/reparse points in every existing ancestor, without resolving."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise LifecycleError('路径包含链接或重解析点，已停止：' + str(part))
    return path


def _identity(path):
    info = _plain(path).stat()
    return [info.st_dev, info.st_ino]


def _digest(path):
    h = hashlib.sha256()
    with _plain(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _json(path, limit=MAX_JSON):
    path = _plain(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise LifecycleError('记录文件类型或大小无效。')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise LifecycleError('读取期间记录发生变化，请重试。')
    if len(raw) > limit:
        raise LifecycleError('记录文件过大。')
    return json.loads(raw)


def _write_json(path, data):
    path = _plain(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _files(root):
    root = _plain(root)
    for directory, folders, names in os.walk(root, followlinks=False):
        for name in folders:
            _plain(Path(directory) / name)
        for name in names:
            path = _plain(Path(directory) / name)
            if not path.is_file():
                raise LifecycleError('安装目录包含非常规文件。')
            yield path


def registry_snapshot():
    import winreg
    result = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            if winreg.QueryInfoKey(key)[0]:
                raise LifecycleError('卸载注册项包含未知子项。')
            for index in range(winreg.QueryInfoKey(key)[1]):
                name, value, kind = winreg.EnumValue(key, index)
                if isinstance(value, bytes):
                    value = {'base64': base64.b64encode(value).decode('ascii')}
                result.append([name, value, kind])
    except FileNotFoundError:
        pass
    return result


def restore_registry(values):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        names = [winreg.EnumValue(key, i)[0] for i in range(winreg.QueryInfoKey(key)[1])]
        for name in names:
            winreg.DeleteValue(key, name)
        for name, value, kind in values:
            if isinstance(value, dict):
                value = base64.b64decode(value['base64'], validate=True)
            winreg.SetValueEx(key, name, 0, kind, value)


def verify_install(app, registration):
    app = _plain(app)
    if app == app.parent or app == Path.home() or len(app.parts) < 3:
        raise LifecycleError('不能在此目录安装。')
    if not app.exists() or not any(app.iterdir()):
        return None
    registered = next((value for name, value, _ in registration if name == 'Inno Setup: App Path'), None)
    if not isinstance(registered, str) or os.path.normcase(os.path.abspath(registered)) != os.path.normcase(str(app)):
        raise LifecycleError('目标不是已登记的写作工作台安装目录。请选择空目录；原目录未改动。')
    manifest = _json(app / 'build-manifest.json')
    if (not isinstance(manifest, dict) or manifest.get('platform') != 'windows-x86_64' or
            not re.fullmatch(r'0\.(?:1|2)\.\d+', str(manifest.get('client_version', '')))):
        raise LifecycleError('旧安装清单无法确认，原目录未改动。')
    files = manifest.get('files')
    required = {'StoryWorkbench.exe', 'backend_runner.py', 'runtime/python.exe', 'Scripts/story.py'}
    if not isinstance(files, dict) or not required.issubset(files) or not (app / 'unins000.exe').is_file():
        raise LifecycleError('旧安装不完整，原目录未改动。')
    # Check every installer-owned file, including the actual interpreter used by
    # services. Unknown author files are preserved by moving the whole directory.
    for name, digest in files.items():
        parts = name.split('/') if isinstance(name, str) else []
        if (not parts or any(p in ('', '.', '..') or '\\' in p or ':' in p for p in parts)
                or not re.fullmatch('[0-9a-f]{64}', str(digest))):
            raise LifecycleError('旧安装清单路径或摘要无效。')
        if _digest(app.joinpath(*parts)) != digest:
            raise LifecycleError('旧安装文件已变化，原目录未改动：' + name)
    list(_files(app))
    return {'directory_identity': _identity(app), 'manifest_sha256': _digest(app / 'build-manifest.json')}


def _kernel():
    if sys.platform != 'win32':
        raise LifecycleError('安装生命周期操作仅支持 Windows。')
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.CreateFileW.restype = wintypes.HANDLE
    return k


def processes_in(app):
    k = _kernel()
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    snapshot = k.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    found = {}
    prefix = os.path.normcase(str(_plain(app))) + os.sep
    try:
        item = Entry(dwSize=ctypes.sizeof(Entry))
        has_item = k.Process32FirstW(snapshot, ctypes.byref(item))
        while has_item:
            handle = k.OpenProcess(0x1000, False, item.th32ProcessID)
            if handle:
                try:
                    image, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                    if k.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                        path = os.path.normcase(os.path.abspath(image.value))
                        if path.startswith(prefix):
                            found[item.th32ProcessID] = path
                finally:
                    k.CloseHandle(handle)
            has_item = k.Process32NextW(snapshot, ctypes.byref(item))
    finally:
        k.CloseHandle(snapshot)
    return found


def probe_file_locks(app, uninstall=False):
    k = _kernel()
    for path in _files(app):
        # Inno's original uninstaller may remain alive while its temporary copy
        # runs. Inno owns the removal of these two files after this hook returns.
        if uninstall and path.parent == app and path.name in ('unins000.exe', 'unins000.dat'):
            continue
        handle = k.CreateFileW(str(path), 0x10000 | 0x80000000, 7, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            raise LifecycleError('文件仍被占用或不可安全替换，请关闭相关程序后重试：' + str(path))
        k.CloseHandle(handle)


def _service_candidates(state_dir):
    state_dir = _plain(state_dir)
    candidates = [(state_dir / 'service.json', state_dir, True)]
    registry = state_dir / 'library.json'
    if registry.exists():
        data = _json(registry)
        if not isinstance(data, dict) or not isinstance(data.get('books'), list):
            raise LifecycleError('书架登记文件格式无效，不能安全确认作品服务。')
        for book in data['books']:
            if not isinstance(book, dict) or not isinstance(book.get('root'), str):
                raise LifecycleError('作品登记路径无效。')
            root = _plain(Path(book['root']))
            candidates.append((root / '.story/workbench-service.json', root, False))
    return candidates


def _service_request(record, root, library, stop=False):
    connection = http.client.HTTPConnection('127.0.0.1', record['port'], timeout=3)
    token, instance = record['token'], record['instance']
    path = '/api/' if library else '/' + token + '/api/'
    body = json.dumps({'instance': instance, 'saved': stop}).encode('utf-8')
    try:
        connection.request('POST', path + ('stop' if stop else 'session'), body,
            {'Content-Type': 'application/json', 'Origin': 'http://127.0.0.1:' + str(record['port']),
             'X-Story-Token': token})
        response = connection.getresponse()
        raw = response.read(8193)
        if response.status != 200 or len(raw) > 8192:
            raise LifecycleError('服务身份认证失败；未强制结束进程。')
        result = json.loads(raw)
    finally:
        connection.close()
    if not isinstance(result, dict):
        raise LifecycleError('服务返回的身份格式无效。')
    matches = (result.get('ok') is True and result.get('running') is True and result.get('instance') == instance and
               result.get('pid') == record['pid'])
    if library:
        matches = matches and result.get('state_dir') == str(root) and result.get('mode') == 'library'
    else:
        matches = matches and result.get('root') == str(root) and result.get('book_id') == record.get('book_id')
    if not matches or (stop and result.get('stop_requested') is not True):
        raise LifecycleError('服务身份或停止回执不匹配；未继续替换文件。')
    return result


def quiesce(app, state_dir, saved=False, uninstall=False, process_list=processes_in,
            request=_service_request, lock_probe=probe_file_locks, timeout=12, clock=time.monotonic,
            sleep=time.sleep):
    app = _plain(app)
    def current_processes():
        found = process_list(app)
        if uninstall:
            found = {pid: image for pid, image in found.items()
                     if image != os.path.normcase(str(app / 'unins000.exe'))}
        return found
    host = os.path.normcase(str(app / 'StoryWorkbench.exe'))
    deadline = clock() + timeout
    processes = current_processes()
    while host in processes.values() and clock() < deadline:
        sleep(0.1)
        processes = current_processes()
    authenticated = {}
    if processes:
        for path, root, library in _service_candidates(state_dir):
            if not path.exists():
                continue
            record_digest = _digest(path)
            record = _json(path, 8192)
            if not isinstance(record, dict) or record.get('pid') not in processes:
                continue
            if (type(record.get('pid')) is not int or
                    processes[record['pid']] != os.path.normcase(str(app / 'runtime/python.exe')) or
                    record.get('state_dir' if library else 'root') != str(root) or type(record.get('port')) is not int or
                    not 1 <= record['port'] <= 65535 or
                    not re.fullmatch(r'[A-Za-z0-9_-]{43}', str(record.get('token', ''))) or
                    not re.fullmatch(r'[0-9a-f]{32}', str(record.get('instance', ''))) or
                    (library and record.get('mode') != 'library') or record.get('stopped')):
                raise LifecycleError('正在运行的服务记录无法确认，请保存后从工作台正常停止服务。')
            if _digest(path) != record_digest:
                raise LifecycleError('服务记录已变化，请重新检查。')
            request(record, root, library)
            if _digest(path) != record_digest:
                raise LifecycleError('服务记录已变化，请重新检查。')
            if record['pid'] in authenticated:
                raise LifecycleError('服务记录存在重复身份，请先正常停止服务。')
            authenticated[record['pid']] = (path, record_digest, record, root, library)
        if set(processes) != set(authenticated):
            raise LifecycleError('安装目录仍有工作台窗口或未能认证的进程。请保存、关闭窗口并正常停止作品服务后重试。')
        if not saved:
            raise SaveConfirmationRequired('仍有书架或作品服务运行。请先保存工作台和外部浏览器中的全部编辑。')
        # Finish authentication of ALL processes before the first stop; do not
        # stop one service then discover an unrelated/unknown process later.
        if current_processes() != processes:
            raise LifecycleError('进程身份已变化，请重新检查。')
        for path, digest, record, root, library in authenticated.values():
            if _digest(path) != digest:
                raise LifecycleError('服务记录已变化，请重新检查。')
            request(record, root, library, stop=True)
        deadline = clock() + timeout
        while True:
            current = current_processes()
            if not current:
                break
            if clock() >= deadline:
                raise LifecycleError('服务尚未正常退出，未替换或删除安装文件。请稍后重试。')
            sleep(0.1)
    lock_probe(app, uninstall=uninstall)


def _save_transaction(journal, transaction):
    _write_json(journal, transaction)
    # A crash may remove the installer's temporary directory. Keep a durable
    # receipt beside the old directory from BEFORE its first rename.
    app, backup = Path(transaction['app']), Path(transaction['backup'])
    _write_json(app.with_name(app.name + '.migration-' + backup.name.rsplit('-', 1)[-1] + '.json'), transaction)


def check_moved_payload(app, backup):
    # A legacy launcher does not know the new installation mutex. Recheck after
    # the original path disappears, before Inno writes a single new file.
    if processes_in(app) or processes_in(backup):
        raise LifecycleError('迁移期间又有旧版进程启动，已停止安装并保留旧版。')
    probe_file_locks(backup)


def prepare_upgrade(app, journal, registration, verified, shortcuts=()):
    app, journal = _plain(app), _plain(journal)
    if verified is None:
        return {'state': 'fresh', 'app': str(app)}
    if journal.exists():
        raise LifecycleError('已有迁移记录，请先完成恢复。')
    if _identity(app) != verified['directory_identity'] or _digest(app / 'build-manifest.json') != verified['manifest_sha256']:
        raise LifecycleError('预检后安装目录身份已变化，未迁移。')
    backup = app.with_name(app.name + '.previous-' + uuid.uuid4().hex)
    transaction = {'schema': 1, 'state': 'planned', 'app': str(app), 'backup': str(backup),
                   'registration': registration, **verified}
    transaction['shortcuts'] = {}
    for shortcut in shortcuts:
        shortcut = _plain(shortcut)
        if shortcut.exists():
            if shortcut.stat().st_size > 1024 * 1024:
                raise LifecycleError('快捷方式大小异常，未迁移。')
            transaction['shortcuts'][str(shortcut)] = base64.b64encode(shortcut.read_bytes()).decode('ascii')
    _save_transaction(journal, transaction)
    # A same-volume directory rename either succeeds as a whole or leaves the
    # original installation intact; no deletion/copy of legacy components.
    if backup.exists():
        raise LifecycleError('备份路径已存在。')
    os.rename(app, backup)
    transaction['state'] = 'moved'
    _save_transaction(journal, transaction)
    try:
        check_moved_payload(app, backup)
    except (LifecycleError, OSError):
        os.rename(backup, app)
        transaction['state'] = 'restored'
        _save_transaction(journal, transaction)
        raise
    return transaction


def finish_upgrade(journal, commit=False, registry_restore=restore_registry):
    journal = _plain(journal)
    if not journal.exists():
        return {'state': 'fresh'}
    transaction = _json(journal)
    if transaction.get('schema') != 1 or transaction.get('state') not in ('planned', 'moved', 'committed', 'restored'):
        raise LifecycleError('迁移记录格式无效。')
    app, backup = _plain(Path(transaction['app'])), _plain(Path(transaction['backup']))
    if backup.parent != app.parent or not backup.name.startswith(app.name + '.previous-'):
        raise LifecycleError('备份路径不属于当前迁移。')
    if transaction['state'] in ('committed', 'restored'):
        return transaction
    restored = not backup.exists() and app.exists() and _identity(app) == transaction['directory_identity']
    if not restored:
        if (_identity(backup) != transaction['directory_identity'] or
                _digest(backup / 'build-manifest.json') != transaction['manifest_sha256']):
            raise LifecycleError('备份身份已变化；保留原路径，请人工核对：' + str(backup))
    if commit:
        if restored or not (app / 'build-manifest.json').is_file():
            raise LifecycleError('新版安装尚未完整，不能提交迁移。')
        transaction['state'] = 'committed'
    else:
        if not restored:
            if app.exists():
                failed = app.with_name(app.name + '.incomplete-' + uuid.uuid4().hex)
                os.rename(app, failed)
                transaction['incomplete'] = str(failed)
                _save_transaction(journal, transaction)
            os.rename(backup, app)
        registry_restore(transaction['registration'])
        for name, encoded in transaction.get('shortcuts', {}).items():
            path, content = _plain(Path(name)), base64.b64decode(encoded, validate=True)
            if path.exists() and path.read_bytes() != content:
                os.rename(path, path.with_name(path.name + '.incomplete-' + uuid.uuid4().hex))
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(content)
        transaction['state'] = 'restored'
    _save_transaction(journal, transaction)
    return transaction


def stage_uninstaller(app, destination):
    app, destination = _plain(app), _plain(destination)
    if destination.exists():
        raise LifecycleError('临时运行目录已存在。')
    list(_files(app / 'runtime'))
    destination.mkdir()
    shutil.copytree(app / 'runtime', destination / 'runtime')
    shutil.copy2(_plain(app / 'installation_lifecycle.py'), destination / 'installation_lifecycle.py')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'prepare', 'commit', 'rollback', 'stage'))
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--journal', type=Path)
    parser.add_argument('--state-dir', type=Path, default=Path.home() / '.codex/story-workbench')
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--saved', action='store_true')
    parser.add_argument('--uninstall', action='store_true')
    parser.add_argument('--shortcut', type=Path, action='append', default=[])
    args = parser.parse_args(argv)
    code = 0
    try:
        if args.action == 'stage':
            stage_uninstaller(args.app, args.destination)
            result = {'ok': True}
        elif args.action in ('commit', 'rollback'):
            result = {'ok': True, **finish_upgrade(args.journal, commit=args.action == 'commit')}
        else:
            registration = registry_snapshot()
            verified = verify_install(args.app, registration)
            if verified is not None:
                quiesce(args.app, args.state_dir, saved=args.saved, uninstall=args.uninstall)
            result = {'ok': True, 'state': 'ready'}
            if args.action == 'prepare':
                result.update(prepare_upgrade(args.app, args.journal, registration, verified, args.shortcut))
    except SaveConfirmationRequired as error:
        code, result = 10, {'ok': False, 'message': str(error)}
    except (LifecycleError, OSError, ValueError, TypeError, KeyError, http.client.HTTPException) as error:
        code, result = 1, {'ok': False, 'message': str(error)}
    _write_json(args.report, result)
    # Inno displays this separate UTF-8 text without needing a JSON parser.
    args.report.with_suffix('.txt').write_text(result.get('message', '完成。'), encoding='utf-8-sig')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
